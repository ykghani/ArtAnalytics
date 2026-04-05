"""Te Papa Tongarewa (Museum of New Zealand) museum client.

API: https://data.tepapa.govt.nz/collection/search
  - Requires free API key: https://data.tepapa.govt.nz/docs/
  - Set env var: TEPAPA_API_KEY=your_key
  - Auth method: x-api-key request header
  - POST requests with JSON body
  - Filter: hasRepresentation.rights.allowsDownload = true
"""
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

TEPAPA_SEARCH_URL = "https://data.tepapa.govt.nz/collection/search"


def _extract_downloadable_media(representations: List[Dict]) -> Optional[Dict]:
    """Return first representation where rights.allowsDownload is True."""
    for rep in representations or []:
        rights = rep.get("rights") or {}
        if rights.get("allowsDownload"):
            return rep.get("media") or {}
    return None


def _extract_artist(production: List[Dict]) -> str:
    if not production:
        return "Unknown Artist"
    first = production[0]
    contributor = first.get("contributor") or {}
    return contributor.get("title", "Unknown Artist") or "Unknown Artist"


def _extract_license(representations: List[Dict]) -> str:
    for rep in representations or []:
        rights = rep.get("rights") or {}
        rt = rights.get("rightsType") or {}
        val = rt.get("value", "")
        if val:
            return val
    return ""


class TePapaArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Te Papa Tongarewa artwork metadata."""

    def __init__(self):
        super().__init__("tepapa")

    def create_metadata(self, data: Dict[str, Any]) -> Optional[ArtworkMetadata]:
        try:
            obj_id = data.get("id")
            if not obj_id:
                return None

            representations = data.get("hasRepresentation") or []
            media = _extract_downloadable_media(representations)
            if media is None:
                return None

            content_url = media.get("contentUrl")
            if not content_url:
                return None

            title = data.get("title", "Untitled") or "Untitled"
            artist = _extract_artist(data.get("production") or [])
            license_str = _extract_license(representations)
            is_public_domain = (
                "cc0" in license_str.lower() or "public domain" in license_str.lower()
            )

            keywords = [
                s.get("value", "")
                for s in data.get("subject") or []
                if s.get("value")
            ]

            return ArtworkMetadata(
                id=str(obj_id),
                accession_number=str(obj_id),
                title=title,
                artist=artist,
                date_display=data.get("date"),
                artwork_type=data.get("type"),
                description=data.get("description"),
                keywords=keywords,
                is_public_domain=is_public_domain,
                credit_line=license_str or None,
                primary_image_url=content_url,
                image_urls={"full": content_url},
                image_pixel_width=media.get("width"),
                image_pixel_height=media.get("height"),
            )
        except Exception as e:
            self.logger.error(f"Error creating metadata for Te Papa object: {e}")
            return None


@dataclass
class TePapaProgressState:
    """State for Te Papa download progress tracking."""

    processed_ids: Set[str] = field(default_factory=set)
    success_ids: Set[str] = field(default_factory=set)
    failed_ids: Set[str] = field(default_factory=set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory=dict)
    last_from: int = 0
    total_objects: int = 0


class TePapaProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        self.state = TePapaProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "tepapa")

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "last_from": self.state.last_from,
            "total_objects": self.state.total_objects,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", {})
        self.state.last_from = data.get("last_from", 0)
        self.state.total_objects = data.get("total_objects", 0)


class TePapaClient(MuseumAPIClient):
    """Te Papa Tongarewa (Museum of New Zealand) API Client."""

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[BaseProgressTracker] = None,
    ):
        # Pass None for api_key to base class — Te Papa uses x-api-key header, not Authorization
        super().__init__(museum_info=museum_info, api_key=None, cache_file=cache_file)
        self.progress_tracker = progress_tracker
        self.artwork_factory = TePapaArtworkFactory()
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "tepapa")
        if api_key:
            self.session.headers.update({"x-api-key": api_key})

    def _get_auth_header(self) -> str:
        return ""

    def get_collection_info(self) -> Dict[str, Any]:
        body = {"query": "", "size": 1, "from": 0}
        resp = self.session.post(TEPAPA_SEARCH_URL, json=body, timeout=30)
        resp.raise_for_status()
        meta = resp.json().get("_metadata") or {}
        total = (meta.get("resultset") or {}).get("count", 0)
        return {"total_objects": total}

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        page_size = 100
        start_from = 0
        if self.progress_tracker and isinstance(self.progress_tracker, TePapaProgressTracker):
            start_from = self.progress_tracker.state.last_from

        offset = start_from
        self.logger.info(f"Starting Te Papa collection iteration from offset {offset}")

        body = {
            "query": "",
            "size": page_size,
            "from": offset,
            "filters": [
                {"field": "hasRepresentation.rights.allowsDownload", "keyword": "true"}
            ],
        }
        resp = self.session.post(TEPAPA_SEARCH_URL, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        total = (data.get("_metadata") or {}).get("resultset", {}).get("count", 0)
        self.logger.info(f"Te Papa total downloadable artworks: {total}")

        if self.progress_tracker and isinstance(self.progress_tracker, TePapaProgressTracker):
            self.progress_tracker.state.total_objects = total

        items = data.get("results") or []
        yield from self._process_page(items)
        offset += page_size

        items_processed = page_size

        while offset < total:
            body = {
                "query": "",
                "size": page_size,
                "from": offset,
                "filters": [
                    {"field": "hasRepresentation.rights.allowsDownload", "keyword": "true"}
                ],
            }
            resp = self.session.post(TEPAPA_SEARCH_URL, json=body, timeout=30)
            resp.raise_for_status()
            items = resp.json().get("results") or []
            if not items:
                break

            yield from self._process_page(items)
            offset += page_size
            items_processed += page_size

            if self.progress_tracker and isinstance(self.progress_tracker, TePapaProgressTracker):
                self.progress_tracker.state.last_from = offset

            # Save checkpoint every 1000 items
            if items_processed % 1000 == 0:
                if self.progress_tracker and isinstance(self.progress_tracker, TePapaProgressTracker):
                    self.progress_tracker._save_progress()

            self.logger.progress(f"Te Papa pagination: processed up to offset {offset}/{total}")
            time.sleep(self.museum_info.rate_limit)

    def _process_page(self, items: List[Dict[str, Any]]) -> Iterator[ArtworkMetadata]:
        for item in items:
            item_id = item.get("id")
            if item_id and self.progress_tracker and \
               self.progress_tracker.is_processed(str(item_id)):
                continue
            metadata = self.artwork_factory.create_metadata(item)
            if metadata:
                yield metadata

    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        url = f"{self.museum_info.base_url}/{artwork_id}"
        resp = self.session.get(url)
        resp.raise_for_status()
        return self.artwork_factory.create_metadata(resp.json())


class TePapaImageProcessor(MuseumImageProcessor):
    """Image processor for Te Papa Tongarewa artworks."""

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
            raise RuntimeError(f"Failed to process Te Papa object {metadata.id}: {e}")

    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        return sanitize_filename(
            id=f"TePapa_{metadata.id}",
            title=metadata.title,
            artist=metadata.artist,
            max_length=255,
        )
