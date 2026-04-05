"""Library of Congress museum client.

Collection: Prints & Photographs (https://www.loc.gov/pictures/?fo=json)
Pagination: sp=1, sp=2, … (page number, c=100 items per page)
Rights filter: items with "no known restrictions" in rights_advisory
Image URL: highest-width file with "image-services/iiif" in URL, or largest JPEG fallback
"""
import re
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

from PIL import Image

from .base import MuseumAPIClient, MuseumImageProcessor
from .schemas import ArtworkMetadata, ArtworkMetadataFactory, MuseumInfo
from ..config import settings
from ..download.progress_tracker import BaseProgressTracker
from ..utils import sanitize_filename, setup_logging

LOC_SEARCH_URL = "https://www.loc.gov/pictures/"
_LOC_ITEM_ID_RE = re.compile(r"/item/([^/]+)/?$")
_NO_RESTRICTION_PHRASES = (
    "no known restrictions",
    "no known copyright restrictions",
    "public domain",
)


def _extract_item_id(item_url: str) -> str:
    match = _LOC_ITEM_ID_RE.search(item_url)
    return match.group(1) if match else item_url


def _is_public_domain(rights_advisory: str) -> bool:
    lower = (rights_advisory or "").lower()
    return any(phrase in lower for phrase in _NO_RESTRICTION_PHRASES)


def _extract_loc_iiif_url(data: Dict[str, Any]) -> Optional[str]:
    """Return the best IIIF or high-res image URL from the resources list."""
    resources = data.get("resources") or []
    best_url = None
    best_width = 0

    for resource in resources:
        for group in resource.get("files") or []:
            for f in group:
                url = f.get("url", "")
                width = f.get("width", 0) or 0
                if "image-services/iiif" in url and width >= best_width:
                    best_url = url
                    best_width = width

    if best_url:
        return best_url

    # Fallback: largest JPEG
    for resource in resources:
        for group in resource.get("files") or []:
            for f in group:
                url = f.get("url", "")
                width = f.get("width", 0) or 0
                if url.endswith(".jpg") and width >= best_width:
                    best_url = url
                    best_width = width

    return best_url


class LOCArtworkFactory(ArtworkMetadataFactory):
    """Factory for Library of Congress artwork metadata."""

    def __init__(self):
        super().__init__("loc")

    def create_metadata(self, data: Dict[str, Any]) -> Optional[ArtworkMetadata]:
        item_url = data.get("id", "")
        item_id = _extract_item_id(item_url) if item_url else ""
        if not item_id:
            return None

        rights_raw = data.get("rights_advisory")
        if isinstance(rights_raw, list):
            rights_advisory = " ".join(rights_raw)
        else:
            rights_advisory = rights_raw or ""

        if not _is_public_domain(rights_advisory):
            return None

        image_url = _extract_loc_iiif_url(data)
        if not image_url:
            return None

        try:
            contributor = data.get("contributor") or []
            if contributor:
                artist = contributor[0]
                if isinstance(artist, dict):
                    artist = artist.get("title", "Unknown Artist")
            else:
                artist = "Unknown Artist"

            title_raw = data.get("title", "Untitled")
            title = title_raw[0] if isinstance(title_raw, list) else (title_raw or "Untitled")

            date_raw = data.get("date", "")
            date_display = date_raw[0] if isinstance(date_raw, list) else (date_raw or None)

            desc_raw = data.get("description") or []
            description = "; ".join(desc_raw) if desc_raw else None

            subjects = data.get("subject") or []
            keywords = [s if isinstance(s, str) else s.get("title", "") for s in subjects]
            keywords = [k for k in keywords if k]

            return ArtworkMetadata(
                id=item_id,
                accession_number=item_id,
                title=title,
                artist=artist,
                date_display=date_display,
                description=description,
                keywords=keywords,
                is_public_domain=True,
                credit_line="Library of Congress",
                primary_image_url=image_url,
                image_urls={"iiif": image_url},
            )
        except Exception as e:
            self.logger.error(f"Error creating LOC metadata for id={item_id}: {e}")
            return None


@dataclass
class LOCProgressState:
    """State for Library of Congress download progress tracking."""

    processed_ids: Set[str] = field(default_factory=set)
    success_ids: Set[str] = field(default_factory=set)
    failed_ids: Set[str] = field(default_factory=set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory=dict)
    last_page: int = 1
    total_objects: int = 0


class LOCProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        self.state = LOCProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "loc")

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


class LOCClient(MuseumAPIClient):
    """Library of Congress Prints & Photographs API client."""

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[LOCProgressTracker] = None,
    ):
        super().__init__(museum_info=museum_info, api_key=api_key, cache_file=cache_file)
        self.progress_tracker = progress_tracker
        self.artwork_factory = LOCArtworkFactory()
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "loc")

    def _get_auth_header(self) -> str:
        return ""

    def get_collection_info(self) -> Dict[str, Any]:
        resp = self.session.get(LOC_SEARCH_URL, params={"fo": "json", "c": 1, "sp": 1})
        resp.raise_for_status()
        pagination = resp.json().get("pagination") or {}
        return {"total_objects": pagination.get("total", 0)}

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        page_size = 100
        start_page = 1
        if self.progress_tracker and isinstance(self.progress_tracker, LOCProgressTracker):
            start_page = self.progress_tracker.state.last_page

        self.logger.info(f"LOC: starting from page {start_page}")
        page = start_page

        while True:
            resp = self.session.get(
                LOC_SEARCH_URL,
                params={"fo": "json", "c": page_size, "sp": page, "at": "results,pagination"},
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()
            results = body.get("results") or []

            if not results:
                break

            pagination = body.get("pagination") or {}
            if page == start_page and self.progress_tracker:
                if isinstance(self.progress_tracker, LOCProgressTracker):
                    self.progress_tracker.state.total_objects = pagination.get("total", 0)
                    self.logger.info(f"LOC: total={self.progress_tracker.state.total_objects}")

            for item in results:
                item_id = _extract_item_id(item.get("id", ""))
                if self.progress_tracker and self.progress_tracker.is_processed(item_id):
                    continue
                metadata = self.artwork_factory.create_metadata(item)
                if metadata:
                    yield metadata

            if not pagination.get("next"):
                break

            page += 1
            if self.progress_tracker and isinstance(self.progress_tracker, LOCProgressTracker):
                self.progress_tracker.state.last_page = page
                if page % 10 == 0:
                    self.progress_tracker.force_save()

            self.logger.progress(f"LOC: page {page}, total={pagination.get('total', '?')}")
            time.sleep(self.museum_info.rate_limit)

    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        url = f"https://www.loc.gov/item/{artwork_id}/"
        resp = self.session.get(url, params={"fo": "json"})
        resp.raise_for_status()
        item = resp.json().get("item") or {}
        return self.artwork_factory.create_metadata(item)


class LOCImageProcessor(MuseumImageProcessor):
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
            raise RuntimeError(f"Failed to process LOC item {metadata.id}: {e}")

    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        return sanitize_filename(
            id=f"LOC_{metadata.id}",
            title=metadata.title,
            artist=metadata.artist,
            max_length=255,
        )
