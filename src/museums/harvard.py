"""Harvard Art Museums (Fogg, Busch-Reisinger, Sackler) client.

API: `GET https://api.harvardartmuseums.org/object` — official, documented
REST API (see docs/harvard.md). Requires an `apikey` on every call, passed
as a query string parameter (not a header) — the base session's `params`
dict is used to attach it automatically to every request made through
`self.session`, since `requests.Session.params` is merged into each call.

Key constraints from that research (docs/harvard.md):
  - Page-based pagination via `page`/`size` (`size` max 100); iterate
    `page` from 1 through `info.pages`.
  - No single `ispublicdomain` flag — `imagepermissionlevel:0` ("ok to
    display images at any size") plus a resolvable image URL is the
    strongest available open-access signal (§5), applied both as the
    server-side query filter and again client-side in the factory.
  - Rate limit is a stated courtesy of ~2500 calls/day, not a documented
    hard 429 — self-throttled via `museum_info.rate_limit` (~35s/call,
    i.e. 86400s / 2500).
"""
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from .base import MuseumAPIClient, MuseumImageProcessor
from ..config import settings
from ..download.progress_tracker import BaseProgressTracker, ProgressState
from .schemas import ArtworkMetadata, HARVARDArtworkFactory, MuseumInfo
from ..utils import setup_logging

HARVARD_PAGE_SIZE = 100


@dataclass
class HARVARDProgressState(ProgressState):
    """Harvard progress state — inherits 4 base fields, adds page-resume fields."""

    last_page: int = 0
    total_pages: int = 0


class HARVARDProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        # Initialize state before calling super().__init__() since parent's _load_progress()
        # calls restore_state() which needs self.state to exist
        self.state = HARVARDProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        # Override the parent's logger with museum-specific logger
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "harvard")

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "last_page": self.state.last_page,
            "total_pages": self.state.total_pages,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", {})
        self.state.last_page = data.get("last_page", 0)
        self.state.total_pages = data.get("total_pages", 0)

    def note_page(self, page: int, *, total_pages: int = 0) -> None:
        self.state.last_page = page
        if total_pages:
            self.state.total_pages = total_pages


class HARVARDClient(MuseumAPIClient):
    """Harvard Art Museums API Client implementation."""

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[BaseProgressTracker] = None,
    ):
        # api_key intentionally withheld from the base class: Harvard authenticates
        # via an `apikey` query param, not an Authorization header (see
        # _get_auth_header). It's attached to session.params below instead, which
        # `requests` merges into every request made through this session.
        super().__init__(
            museum_info=museum_info, api_key=None, cache_file=cache_file
        )
        self.progress_tracker = progress_tracker
        self.artwork_factory = HARVARDArtworkFactory()
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "harvard")
        if api_key:
            self.session.params = {"apikey": api_key}

    def _get_auth_header(self) -> str:
        """Harvard authenticates via an `apikey` query param, not a header."""
        return ""

    def get_collection_info(self) -> Dict[str, Any]:
        """Get basic collection information for the configured query."""
        url = f"{self.museum_info.base_url}/object"
        response = self.session.get(url, params={"size": 1}, timeout=(5, 30))
        response.raise_for_status()
        total = response.json().get("info", {}).get("totalrecords", 0)
        return {"total_objects": total}

    def _get_object_page(self, page: int, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.museum_info.base_url}/object"
        page_params = {**params, "size": HARVARD_PAGE_SIZE, "page": page}
        self.logger.debug(f"Requesting url: {url} with params: {page_params}")
        response = self.session.get(url, params=page_params, timeout=(5, 30))
        response.raise_for_status()
        return response.json()

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        """Paginate `/object` from the last saved page through `info.pages`."""
        page = 1
        if self.progress_tracker and isinstance(self.progress_tracker, HARVARDProgressTracker):
            page = max(1, self.progress_tracker.state.last_page)

        total_pages: Optional[int] = None

        while True:
            try:
                data = self._get_object_page(page, params)
            except Exception as e:
                self.logger.error(f"Error fetching page {page}: {e}")
                raise

            info = data.get("info", {})
            if total_pages is None:
                total_pages = info.get("pages", 0)

            records = data.get("records") or []
            if not records:
                break

            for record in records:
                metadata = self.artwork_factory.create_metadata(record)
                if metadata:
                    yield metadata

            if self.progress_tracker:
                self.progress_tracker.note_page(page, total_pages=total_pages)
                if isinstance(self.progress_tracker, HARVARDProgressTracker):
                    self.progress_tracker.force_save()

            if total_pages and page >= total_pages:
                break

            page += 1
            time.sleep(self.museum_info.rate_limit)

    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        """Fetch a single object by objectid."""
        url = f"{self.museum_info.base_url}/object/{artwork_id}"

        try:
            self.logger.debug(f"Fetching artwork details from: {url}")
            response = self.session.get(url, timeout=(5, 30))
            response.raise_for_status()
            return self.artwork_factory.create_metadata(response.json())
        except Exception as e:
            self.logger.error(f"Error fetching details for artwork {artwork_id}: {e}")
            raise


class HARVARDImageProcessor(MuseumImageProcessor):
    """Harvard Art Museums image processor implementation.

    Uses the base class's generate_filename/process_image as-is: the default
    `HARVARD_` prefix (museum_info.code.upper()) already matches what's required.
    """

    def __init__(self, output_dir: Path, museum_info: MuseumInfo):
        super().__init__(output_dir, museum_info)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "harvard")
