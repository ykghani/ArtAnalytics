"""Kunstmuseum Basel client.

Data source: no documented REST API. Object detail pages are server-rendered
by a Next.js app with the full record embedded as JSON in a
`<script id="__NEXT_DATA__" type="application/json">` tag:

    GET https://sammlung.kunstmuseumbasel.ch/{lng}/collection/item/{id}

See docs/kunstmuseum-basel.md for the full writeup this implementation is
based on. Key constraints from that research:

  - No auth required.
  - `{id}` is an internal numeric object ID (opaque, not the human-readable
    inventory number). Invalid IDs return a clean 404 whose `__NEXT_DATA__`
    still parses, with `pageProps.pageKey == "404"`.
  - The search/listing page's results are fetched client-side (not SSR'd) by
    an endpoint that couldn't be identified from the shipped JS bundles
    (§3) — so there is no real pagination to walk. Discovery instead uses a
    sequential numeric ID sweep against the item route, bounded by a fixed
    configurable upper bound (`kunstmuseumbasel_max_sweep_id`) rather than
    inferred from a run of consecutive 404s, since gaps in the ID space are
    expected.
  - Images require a same-origin `Referer` header or the host returns 403
    (§6) — set once on the session so it's applied to every request,
    including image downloads made via `client.session.get(...)`.
"""
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Set

import requests

from .base import MuseumAPIClient, MuseumImageProcessor
from .schemas import ArtworkMetadata, KUNSTMUSEUMBASEL_BASE_URL, KunstmuseumBaselArtworkFactory, MuseumInfo
from ..config import settings
from ..download.progress_tracker import BaseProgressTracker
from ..utils import setup_logging

KUNSTMUSEUMBASEL_ITEM_URL_TMPL = KUNSTMUSEUMBASEL_BASE_URL + "/{lng}/collection/item/{id}"
KUNSTMUSEUMBASEL_LNG = "en"

# How often (in ids swept) to checkpoint sweep progress to disk. A plain
# force_save() per id would be far too much I/O given the sweep is expected
# to spend most of its time on 404s (§3, §8).
SWEEP_CHECKPOINT_INTERVAL = 100

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)


def _parse_next_data(html: str) -> Optional[Dict[str, Any]]:
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _extract_item(next_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the item record from a parsed `__NEXT_DATA__` document, or
    None for a (cleanly-detectable, per §2) 404 page."""
    page_props = (next_data.get("props") or {}).get("pageProps") or {}
    if page_props.get("pageKey") == "404":
        return None
    data = page_props.get("data") or {}
    return data.get("item")


@dataclass
class KunstmuseumBaselProgressState:
    """State for Kunstmuseum Basel download progress tracking."""

    processed_ids: Set[str] = field(default_factory=set)
    success_ids: Set[str] = field(default_factory=set)
    failed_ids: Set[str] = field(default_factory=set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory=dict)
    next_id: int = 1


class KunstmuseumBaselProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        self.state = KunstmuseumBaselProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "kunstmuseumbasel")

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "next_id": self.state.next_id,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", {})
        self.state.next_id = data.get("next_id", 1)


class KunstmuseumBaselClient(MuseumAPIClient):
    """Kunstmuseum Basel client — sequential numeric-ID sweep over SSR item pages."""

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[KunstmuseumBaselProgressTracker] = None,
    ):
        super().__init__(museum_info=museum_info, api_key=api_key, cache_file=cache_file)
        self.progress_tracker = progress_tracker
        self.artwork_factory = KunstmuseumBaselArtworkFactory()
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "kunstmuseumbasel")
        self.max_sweep_id = settings.kunstmuseumbasel_max_sweep_id

    def _customize_session(self, session: requests.Session) -> None:
        # Hotlink protection (§6) requires a same-origin Referer on image
        # requests; applying it session-wide also covers item-page GETs
        # harmlessly, and covers image downloads made via client.session.get
        # in ArtworkDownloader without that code needing to know about it.
        session.headers.update({"Referer": f"{self.museum_info.base_url}/"})

    def _get_auth_header(self) -> str:
        """Kunstmuseum Basel's SSR item pages require no authentication."""
        return ""

    def _fetch_item(self, artwork_id: str) -> Optional[Dict[str, Any]]:
        url = KUNSTMUSEUMBASEL_ITEM_URL_TMPL.format(lng=KUNSTMUSEUMBASEL_LNG, id=artwork_id)
        try:
            response = self.session.get(url, timeout=30)
        except requests.exceptions.RequestException as e:
            self.logger.warning(f"Kunstmuseum Basel: request failed for id {artwork_id}: {e}")
            return None

        if response.status_code == 404:
            return None
        if response.status_code != 200:
            self.logger.debug(
                f"Kunstmuseum Basel: unexpected status {response.status_code} for id {artwork_id}"
            )
            return None

        next_data = _parse_next_data(response.text)
        if not next_data:
            self.logger.debug(f"Kunstmuseum Basel: no __NEXT_DATA__ found for id {artwork_id}")
            return None

        return _extract_item(next_data)

    def get_collection_info(self) -> Dict[str, Any]:
        """No real collection total is discoverable (§3, §8) — report the
        configured sweep upper bound rather than an unverified estimate."""
        return {"total_objects": self.max_sweep_id}

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        max_id = params.get("max_id") or self.max_sweep_id
        start_id = 1
        if self.progress_tracker and isinstance(self.progress_tracker, KunstmuseumBaselProgressTracker):
            start_id = max(self.progress_tracker.state.next_id, 1)

        self.logger.progress(f"Kunstmuseum Basel: sweeping ids {start_id}..{max_id}")

        swept = 0
        for artwork_id in range(start_id, max_id + 1):
            str_id = str(artwork_id)
            if not (self.progress_tracker and self.progress_tracker.is_processed(str_id)):
                item = self._fetch_item(str_id)
                if item:
                    metadata = self.artwork_factory.create_metadata(item)
                    if metadata:
                        yield metadata

                time.sleep(self.museum_info.rate_limit)

            swept += 1
            if self.progress_tracker and isinstance(self.progress_tracker, KunstmuseumBaselProgressTracker):
                self.progress_tracker.state.next_id = artwork_id + 1
                if swept % SWEEP_CHECKPOINT_INTERVAL == 0:
                    self.progress_tracker.force_save()

        if self.progress_tracker and isinstance(self.progress_tracker, KunstmuseumBaselProgressTracker):
            self.progress_tracker.force_save()

        self.logger.progress(f"Kunstmuseum Basel: sweep finished at id {max_id}")

    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        item = self._fetch_item(str(artwork_id))
        if not item:
            return None
        return self.artwork_factory.create_metadata(item)


class KunstmuseumBaselImageProcessor(MuseumImageProcessor):
    """Kunstmuseum Basel image processor.

    Uses the base class's generate_filename/process_image as-is: the default
    `KUNSTMUSEUMBASEL_` prefix (museum_info.code.upper()) already matches
    what's required. The `Referer` header needed to fetch the image itself
    (§6) is applied session-wide by KunstmuseumBaselClient._customize_session,
    not here — this processor only handles bytes already downloaded by the
    shared ArtworkDownloader.
    """

    def __init__(self, output_dir: Path, museum_info: MuseumInfo):
        super().__init__(output_dir, museum_info)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "kunstmuseumbasel")
