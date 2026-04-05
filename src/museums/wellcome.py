"""Wellcome Collection museum client.

API: https://api.wellcomecollection.org/catalogue/v2/images
  - No authentication required
  - Pagination via cursor (pageAfter param extracted from nextPage URL)
  - License: mostly CC-BY (not CC0/public domain, but freely downloadable)
  - IIIF image URL: https://iiif.wellcomecollection.org/image/{id}/full/max/0/default.jpg
"""
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Set
from urllib.parse import urlparse, parse_qs

from PIL import Image

from .base import MuseumAPIClient, MuseumImageProcessor
from .schemas import ArtworkMetadata, ArtworkMetadataFactory, MuseumInfo
from ..config import settings
from ..download.progress_tracker import BaseProgressTracker
from ..utils import sanitize_filename, setup_logging

WELLCOME_IMAGES_URL = "https://api.wellcomecollection.org/catalogue/v2/images"
WELLCOME_IIIF_BASE = "https://iiif.wellcomecollection.org/image"


def _wellcome_iiif_url(image_id: str) -> str:
    return f"{WELLCOME_IIIF_BASE}/{image_id}/full/max/0/default.jpg"


class WellcomeArtworkFactory(ArtworkMetadataFactory):
    """Factory for Wellcome Collection artwork metadata."""

    def __init__(self):
        super().__init__("wellcome")

    def create_metadata(self, data: Dict[str, Any]) -> Optional[ArtworkMetadata]:
        image_id = data.get("id", "").strip()
        if not image_id:
            return None

        try:
            source = data.get("source") or {}
            title = source.get("title", "Untitled") or "Untitled"

            contributors = source.get("contributors") or []
            if contributors:
                artist = contributors[0].get("agent", {}).get("label", "Unknown Artist")
            else:
                artist = "Unknown Artist"

            dates = source.get("dates") or []
            date_display = dates[0].get("label") if dates else None

            subjects = source.get("subjects") or []
            keywords = [s.get("label") for s in subjects if s.get("label")]

            locations = data.get("locations") or []
            license_id = ""
            if locations:
                license_obj = locations[0].get("license") or {}
                license_id = license_obj.get("id", "")

            is_pd = license_id in ("pdm", "cc0")
            iiif = _wellcome_iiif_url(image_id)

            return ArtworkMetadata(
                id=image_id,
                accession_number=source.get("id", ""),
                title=title,
                artist=artist,
                date_display=date_display,
                description=source.get("description"),
                keywords=keywords,
                is_public_domain=is_pd,
                credit_line=license_id,
                primary_image_url=iiif,
                image_urls={"iiif": iiif},
            )
        except Exception as e:
            self.logger.error(f"Error creating Wellcome metadata for id={image_id}: {e}")
            return None


@dataclass
class WellcomeProgressState:
    """State for Wellcome Collection download progress tracking."""

    processed_ids: Set[str] = field(default_factory=set)
    success_ids: Set[str] = field(default_factory=set)
    failed_ids: Set[str] = field(default_factory=set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory=dict)
    next_cursor: Optional[str] = None
    total_objects: int = 0


class WellcomeProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        self.state = WellcomeProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "wellcome")

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "next_cursor": self.state.next_cursor,
            "total_objects": self.state.total_objects,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", {})
        self.state.next_cursor = data.get("next_cursor")
        self.state.total_objects = data.get("total_objects", 0)


class WellcomeClient(MuseumAPIClient):
    """Wellcome Collection API client — cursor-paginated images endpoint."""

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[WellcomeProgressTracker] = None,
    ):
        super().__init__(museum_info=museum_info, api_key=api_key, cache_file=cache_file)
        self.progress_tracker = progress_tracker
        self.artwork_factory = WellcomeArtworkFactory()
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "wellcome")

    def _get_auth_header(self) -> str:
        return ""

    def get_collection_info(self) -> Dict[str, Any]:
        resp = self.session.get(WELLCOME_IMAGES_URL, params={"pageSize": 1})
        resp.raise_for_status()
        return {"total_objects": resp.json().get("totalResults", 0)}

    def _extract_cursor(self, next_page_url: Optional[str]) -> Optional[str]:
        """Extract pageAfter value from a nextPage URL."""
        if not next_page_url:
            return None
        parsed = urlparse(next_page_url)
        params = parse_qs(parsed.query)
        cursors = params.get("pageAfter", [])
        return cursors[0] if cursors else None

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        page_size = 100
        cursor = None
        if self.progress_tracker and isinstance(self.progress_tracker, WellcomeProgressTracker):
            cursor = self.progress_tracker.state.next_cursor

        request_params: Dict[str, Any] = {"pageSize": page_size}
        if cursor:
            request_params["pageAfter"] = cursor

        resp = self.session.get(WELLCOME_IMAGES_URL, params=request_params)
        resp.raise_for_status()
        body = resp.json()

        if self.progress_tracker and isinstance(self.progress_tracker, WellcomeProgressTracker):
            self.progress_tracker.state.total_objects = body.get("totalResults", 0)

        self.logger.info(f"Wellcome: totalResults={body.get('totalResults', '?')}")

        while True:
            for item in body.get("results") or []:
                image_id = item.get("id", "")
                if self.progress_tracker and self.progress_tracker.is_processed(image_id):
                    continue
                metadata = self.artwork_factory.create_metadata(item)
                if metadata:
                    yield metadata

            next_page = body.get("nextPage")
            if not next_page:
                break

            cursor = self._extract_cursor(next_page)
            if not cursor:
                break

            if self.progress_tracker and isinstance(self.progress_tracker, WellcomeProgressTracker):
                self.progress_tracker.state.next_cursor = cursor
                self.progress_tracker.force_save()

            self.logger.progress(f"Wellcome: fetching next page (cursor={cursor[:20]}…)")
            time.sleep(self.museum_info.rate_limit)

            resp = self.session.get(
                WELLCOME_IMAGES_URL,
                params={"pageSize": page_size, "pageAfter": cursor},
            )
            resp.raise_for_status()
            body = resp.json()

    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        url = f"{self.museum_info.base_url}/images/{artwork_id}"
        resp = self.session.get(url)
        resp.raise_for_status()
        return self.artwork_factory.create_metadata(resp.json())


class WellcomeImageProcessor(MuseumImageProcessor):
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
            raise RuntimeError(f"Failed to process Wellcome image {metadata.id}: {e}")

    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        return sanitize_filename(
            id=f"Wellcome_{metadata.id}",
            title=metadata.title,
            artist=metadata.artist,
            max_length=255,
        )
