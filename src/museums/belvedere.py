"""Belvedere museum (Vienna) client.

Collection: Open Content images (https://sammlung.belvedere.at/opencontent)
Platform: Gallery Systems eMuseum, IIIF Presentation API v2 + Image API v2.

The full IIIF collection (/apis/iiif/presentation/v2/collection/module/objects)
contains ~14,841 manifests, but many are still rights-restricted — only the
~5,897 items listed on the "Open Content" browse pages are CC0. There is no
JSON facet that isolates that subset, so object ids are scraped from the
paginated HTML listing at /opencontent/images?page=N, and each id's manifest
is then fetched individually to get metadata + the full-resolution image URL.

robots.txt publishes `Crawl-delay: 30` for this host — belvedere_rate_limit
defaults to 30.0 and a sleep is applied after every request (listing page and
manifest fetch alike), not just once per page.
"""
import re
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from PIL import Image

from .base import MuseumAPIClient, MuseumImageProcessor
from .schemas import ArtworkMetadata, ArtworkMetadataFactory, MuseumInfo
from ..config import settings
from ..download.progress_tracker import BaseProgressTracker
from ..utils import sanitize_filename, setup_logging

BELVEDERE_BASE_URL = "https://sammlung.belvedere.at"
BELVEDERE_LISTING_URL = f"{BELVEDERE_BASE_URL}/opencontent/images"
BELVEDERE_MANIFEST_URL_TMPL = (
    BELVEDERE_BASE_URL + "/apis/iiif/presentation/v2/1-objects-{object_id}/manifest"
)

_OBJECT_LINK_RE = re.compile(r'href="/objects/(\d+)/')
_TOTAL_COUNT_RE = re.compile(r"(?:of|von)\s+([\d.,]+)")
_YEAR_RE = re.compile(r"\A\d{4}\Z")


def _extract_object_ids(html: str) -> List[str]:
    """Return unique object ids from a /opencontent/images listing page, in order."""
    return list(dict.fromkeys(_OBJECT_LINK_RE.findall(html)))


def _extract_total_count(html: str) -> Optional[int]:
    match = _TOTAL_COUNT_RE.search(html)
    if not match:
        return None
    digits = re.sub(r"[.,]", "", match.group(1))
    return int(digits) if digits.isdigit() else None


def _is_rights_restricted(attribution: Any) -> bool:
    """Belvedere's open-content items have attribution "null" or empty; anything
    else is a real rights holder that must not be treated as public domain."""
    text = (attribution or "").strip().lower()
    return text not in ("", "null")


def _extract_manifest_image(manifest: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Return (canvas_label, image_url) for the first canvas of a manifest, or None."""
    sequences = manifest.get("sequences") or []
    if not sequences:
        return None
    canvases = sequences[0].get("canvases") or []
    if not canvases:
        return None
    canvas = canvases[0]
    images = canvas.get("images") or []
    if not images:
        return None
    image_url = (images[0].get("resource") or {}).get("@id")
    if not image_url:
        return None
    label = canvas.get("label") or manifest.get("label") or ""
    return label, image_url


def _parse_canvas_label(label: str) -> Tuple[str, str, Optional[str]]:
    """Best-effort split of a Belvedere canvas label into (artist, title, date_display).

    Labels look like: 'Klaus Basset, 1968, Papier, Blattmaße: 41,3 x 30,3 cm,
    Belvedere, Wien, Inv.-Nr. 11686/17' — comma-separated, artist first, an
    optional quoted title, a 4-digit year, then medium/dimensions/credit/invno.
    Falls back to the whole label as title when nothing better is found.
    """
    parts = [p.strip() for p in label.split(",")]
    artist = parts[0] if parts and parts[0] else "Unknown Artist"

    title = None
    date_display = None
    for part in parts[1:]:
        if title is None and '"' in part:
            title = part
        if date_display is None and _YEAR_RE.match(part):
            date_display = part

    if title is None:
        title = label or "Untitled"

    return artist, title, date_display


class BelvedereArtworkFactory(ArtworkMetadataFactory):
    """Factory for Belvedere artwork metadata built from an IIIF manifest."""

    def __init__(self):
        super().__init__("belvedere")

    def create_metadata(self, manifest: Dict[str, Any], object_id: str) -> Optional[ArtworkMetadata]:
        if _is_rights_restricted(manifest.get("attribution")):
            return None

        extracted = _extract_manifest_image(manifest)
        if not extracted:
            return None
        label, image_url = extracted

        try:
            artist, title, date_display = _parse_canvas_label(label)
            accession_match = re.search(r"Inv\.-Nr\.\s*(\S+)", label)
            accession_number = (
                accession_match.group(1).rstrip(".,")
                if accession_match
                else f"BELVEDERE-{object_id}"
            )

            return ArtworkMetadata(
                id=object_id,
                accession_number=accession_number,
                title=title,
                artist=artist,
                date_display=date_display,
                description=label or None,
                is_public_domain=True,
                credit_line="Belvedere, Wien",
                primary_image_url=image_url,
                image_urls={"iiif": image_url},
            )
        except Exception as e:
            self.logger.error(f"Error creating Belvedere metadata for id={object_id}: {e}")
            return None


@dataclass
class BelvedereProgressState:
    """State for Belvedere download progress tracking."""

    processed_ids: Set[str] = field(default_factory=set)
    success_ids: Set[str] = field(default_factory=set)
    failed_ids: Set[str] = field(default_factory=set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory=dict)
    last_page: int = 1
    total_objects: int = 0


class BelvedereProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        self.state = BelvedereProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "belvedere")

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "last_page": self.state.last_page,
            "total_objects": self.state.total_objects,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", {})
        self.state.last_page = data.get("last_page", 1)
        self.state.total_objects = data.get("total_objects", 0)


class BelvedereClient(MuseumAPIClient):
    """Belvedere (Vienna) Open Content IIIF client."""

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[BelvedereProgressTracker] = None,
    ):
        super().__init__(museum_info=museum_info, api_key=api_key, cache_file=cache_file)
        self.progress_tracker = progress_tracker
        self.artwork_factory = BelvedereArtworkFactory()
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "belvedere")

    def _get_auth_header(self) -> str:
        return ""

    def get_collection_info(self) -> Dict[str, Any]:
        resp = self.session.get(BELVEDERE_LISTING_URL, params={"page": 1}, timeout=30)
        resp.raise_for_status()
        total = _extract_total_count(resp.text)
        return {"total_objects": total or 0}

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        start_page = 1
        if self.progress_tracker and isinstance(self.progress_tracker, BelvedereProgressTracker):
            start_page = self.progress_tracker.state.last_page

        self.logger.info(f"Belvedere: starting from page {start_page}")
        page = start_page

        while True:
            resp = self.session.get(BELVEDERE_LISTING_URL, params={"page": page}, timeout=30)
            resp.raise_for_status()
            time.sleep(self.museum_info.rate_limit)

            object_ids = _extract_object_ids(resp.text)
            if not object_ids:
                self.logger.info(f"Belvedere: no items on page {page}, stopping")
                break

            if page == start_page and self.progress_tracker:
                if isinstance(self.progress_tracker, BelvedereProgressTracker):
                    total = _extract_total_count(resp.text) or 0
                    self.progress_tracker.state.total_objects = total
                    self.logger.info(f"Belvedere: total open-content items={total}")

            for object_id in object_ids:
                if self.progress_tracker and self.progress_tracker.is_processed(object_id):
                    continue

                manifest_resp = self.session.get(
                    BELVEDERE_MANIFEST_URL_TMPL.format(object_id=object_id), timeout=30
                )
                time.sleep(self.museum_info.rate_limit)
                if manifest_resp.status_code != 200:
                    self.logger.debug(f"Belvedere: manifest fetch failed for {object_id}: {manifest_resp.status_code}")
                    continue

                manifest = manifest_resp.json()
                metadata = self.artwork_factory.create_metadata(manifest, object_id)
                if metadata:
                    yield metadata

            page += 1
            if self.progress_tracker and isinstance(self.progress_tracker, BelvedereProgressTracker):
                self.progress_tracker.state.last_page = page
                self.progress_tracker.force_save()

            self.logger.progress(f"Belvedere: page {page}")

    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        resp = self.session.get(
            BELVEDERE_MANIFEST_URL_TMPL.format(object_id=artwork_id), timeout=30
        )
        resp.raise_for_status()
        manifest = resp.json()
        return self.artwork_factory.create_metadata(manifest, artwork_id)


class BelvedereImageProcessor(MuseumImageProcessor):
    def process_image(self, image_data: bytes, metadata: ArtworkMetadata) -> tuple[Path, int, int]:
        try:
            image = Image.open(BytesIO(image_data))
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGB")
            width, height = image.size
            filename = self.generate_filename(metadata)
            filepath = self.output_dir / filename
            image.save(filepath, format="JPEG", quality=95)
            return filepath, width, height
        except Exception as e:
            raise RuntimeError(f"Failed to process Belvedere item {metadata.id}: {e}")

    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        return sanitize_filename(
            id=f"BELVEDERE_{metadata.id}",
            title=metadata.title,
            artist=metadata.artist,
            max_length=255,
        )
