from typing import Dict, List, Any, Optional, Iterator, Set, Tuple
from pathlib import Path
from PIL import Image
from io import BytesIO
from dataclasses import dataclass, field
import time
import logging

from .base import MuseumAPIClient, MuseumImageProcessor
from ..config import settings
from ..download.progress_tracker import BaseProgressTracker
from .schemas import ArtworkMetadata, MuseumInfo, ArtworkMetadataFactory
from ..utils import sanitize_filename, setup_logging

SMK_SEARCH_FILTERS = "has_image:true,public_domain:true"


def _extract_artist(data: Dict[str, Any]) -> str:
    """Extract primary artist name from SMK response.

    The `artist` field is a flat list of strings. The `production` field has
    richer data (used as fallback for more structured access).
    """
    # `artist` field: list of strings e.g. ['Antonio da Trento', 'Parmigianino']
    artist_list = data.get("artist")
    if isinstance(artist_list, list) and artist_list:
        return artist_list[0]

    # Fallback: `production` list of dicts with `creator` key
    production = data.get("production") or []
    if production and isinstance(production[0], dict):
        name = production[0].get("creator")
        if name:
            return name

    return "Unknown Artist"


def _extract_year(iso_date: Optional[str]) -> Optional[str]:
    """Extract 4-digit year from ISO 8601 date string like '1500-01-01T00:00:00.000Z'."""
    if not iso_date:
        return None
    try:
        return iso_date[:4]
    except (TypeError, IndexError):
        return None


def _extract_dimensions(data: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Extract height, width, depth in cm from SMK dimensions list.

    SMK dimensions have string values and units in mm.
    """
    dims = data.get("dimensions") or []
    result = {"height": None, "width": None, "depth": None}

    for dim in dims:
        if not isinstance(dim, dict):
            continue
        dim_type = (dim.get("type") or "").lower()
        value = dim.get("value")
        unit = (dim.get("unit") or "cm").lower()

        if dim_type in result and value is not None:
            try:
                cm_value = float(value)
                if unit == "mm":
                    cm_value /= 10
                result[dim_type] = cm_value
            except (TypeError, ValueError):
                pass

    return result["height"], result["width"], result["depth"]


def _extract_artwork_type(data: Dict[str, Any]) -> Optional[str]:
    """Extract artwork type from object_names list."""
    object_names = data.get("object_names") or []
    if object_names and isinstance(object_names[0], dict):
        return object_names[0].get("name")
    return data.get("object_type")


class SMKArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Statens Museum for Kunst artwork metadata."""

    def __init__(self):
        super().__init__("smk")

    def create_metadata(self, data: Dict[str, Any]) -> Optional[ArtworkMetadata]:
        try:
            object_number = data.get("object_number")
            if not object_number:
                return None

            # Only public domain artworks
            if not data.get("public_domain", False):
                return None

            # Image URL — required; prefer IIIF, fall back to image_native
            iiif_id = data.get("image_iiif_id")
            image_native = data.get("image_native")
            if iiif_id:
                primary_image_url = f"{iiif_id}/full/max/0/default.jpg"
            elif image_native:
                primary_image_url = image_native
            else:
                return None

            # Title from titles list
            titles = data.get("titles") or []
            title = (titles[0].get("title") if titles else None) or "Untitled"

            artist = _extract_artist(data)

            # Richer artist info from production list
            production = data.get("production") or []
            artist_nationality = None
            artist_birth_year = None
            artist_death_year = None
            if production and isinstance(production[0], dict):
                primary = production[0]
                artist_nationality = primary.get("creator_nationality")
                birth = _extract_year(primary.get("creator_date_of_birth"))
                death = _extract_year(primary.get("creator_date_of_death"))
                artist_birth_year = int(birth) if birth else None
                artist_death_year = int(death) if death else None

            # Dates: production_date has ISO strings, extract year
            prod_dates = data.get("production_date") or []
            date_start = None
            date_end = None
            date_display = None
            if prod_dates:
                first = prod_dates[0]
                # Use period string if available, else build from start/end years
                period = first.get("period")
                if period:
                    date_display = period
                date_start = _extract_year(first.get("start"))
                date_end = _extract_year(first.get("end"))
                if not date_display:
                    if date_start and date_end and date_start != date_end:
                        date_display = f"{date_start}-{date_end}"
                    else:
                        date_display = date_start

            height_cm, width_cm, depth_cm = _extract_dimensions(data)

            keywords = data.get("techniques") or []

            return ArtworkMetadata(
                id=str(object_number),
                accession_number=str(object_number),
                title=title,
                artist=artist,
                artist_nationality=artist_nationality,
                artist_birth_year=artist_birth_year,
                artist_death_year=artist_death_year,
                date_display=date_display,
                date_start=date_start,
                date_end=date_end,
                medium=data.get("medium"),
                height_cm=height_cm,
                width_cm=width_cm,
                depth_cm=depth_cm,
                department=data.get("department"),
                artwork_type=_extract_artwork_type(data),
                is_public_domain=True,
                is_on_view=data.get("on_display"),
                description=data.get("description"),
                keywords=keywords,
                primary_image_url=primary_image_url,
                image_urls={"full": primary_image_url},
                image_pixel_width=data.get("image_width"),
                image_pixel_height=data.get("image_height"),
            )
        except Exception as e:
            self.logger.error(f"Error creating metadata for SMK object: {e}")
            return None


@dataclass
class SMKProgressState:
    """State for SMK download progress tracking."""

    processed_ids: Set[str] = field(default_factory=set)
    success_ids: Set[str] = field(default_factory=set)
    failed_ids: Set[str] = field(default_factory=set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory=dict)
    last_offset: int = 0
    total_objects: int = 0


class SMKProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        self.state = SMKProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "smk")

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "last_offset": self.state.last_offset,
            "total_objects": self.state.total_objects,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", {})
        self.state.last_offset = data.get("last_offset", 0)
        self.state.total_objects = data.get("total_objects", 0)


class SMKClient(MuseumAPIClient):
    """Statens Museum for Kunst API Client."""

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[BaseProgressTracker] = None,
    ):
        super().__init__(museum_info=museum_info, api_key=api_key, cache_file=cache_file)
        self.progress_tracker = progress_tracker
        self.artwork_factory = SMKArtworkFactory()
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "smk")

    def _get_auth_header(self) -> str:
        return ""

    def get_collection_info(self) -> Dict[str, Any]:
        resp = self.session.get(
            f"{self.museum_info.base_url}/art/search/",
            params={"keys": "*", "filters": SMK_SEARCH_FILTERS, "offset": 0, "rows": 1, "lang": "en"},
        )
        resp.raise_for_status()
        return {"total_objects": resp.json().get("found", 0)}

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        rows = 2000
        start_offset = 0
        if self.progress_tracker and isinstance(self.progress_tracker, SMKProgressTracker):
            start_offset = self.progress_tracker.state.last_offset

        offset = start_offset
        self.logger.info(f"Starting SMK collection iteration from offset {offset}")

        # Fetch first page and get total
        resp = self.session.get(
            f"{self.museum_info.base_url}/art/search/",
            params={"keys": "*", "filters": SMK_SEARCH_FILTERS,
                    "offset": offset, "rows": rows, "lang": "en"},
        )
        resp.raise_for_status()
        data = resp.json()
        total = data.get("found", 0)
        self.logger.info(f"SMK total matching artworks: {total}")

        if self.progress_tracker and isinstance(self.progress_tracker, SMKProgressTracker):
            self.progress_tracker.state.total_objects = total

        items = data.get("items") or []
        yield from self._process_page(items)
        offset += rows

        while offset < total:
            resp = self.session.get(
                f"{self.museum_info.base_url}/art/search/",
                params={"keys": "*", "filters": SMK_SEARCH_FILTERS,
                        "offset": offset, "rows": rows, "lang": "en"},
            )
            resp.raise_for_status()
            items = resp.json().get("items") or []
            if not items:
                break

            yield from self._process_page(items)
            offset += rows

            if self.progress_tracker and isinstance(self.progress_tracker, SMKProgressTracker):
                self.progress_tracker.state.last_offset = offset
                self.progress_tracker._save_progress()

            self.logger.progress(f"SMK pagination: processed up to offset {offset}/{total}")
            time.sleep(self.museum_info.rate_limit)

    def _process_page(self, items: List[Dict[str, Any]]) -> Iterator[ArtworkMetadata]:
        for item in items:
            object_number = item.get("object_number")
            if object_number and self.progress_tracker and \
               self.progress_tracker.is_processed(str(object_number)):
                continue
            metadata = self.artwork_factory.create_metadata(item)
            if metadata:
                yield metadata

    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        resp = self.session.get(
            f"{self.museum_info.base_url}/art/",
            params={"object_number": artwork_id, "lang": "en"},
        )
        resp.raise_for_status()
        items = resp.json()
        if not items:
            return None
        return self.artwork_factory.create_metadata(items[0])


class SMKImageProcessor(MuseumImageProcessor):
    """Image processor for SMK artworks."""

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
            raise RuntimeError(f"Failed to process SMK object {metadata.id}: {e}")

    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        return sanitize_filename(
            id=f"SMK_{metadata.id}",
            title=metadata.title,
            artist=metadata.artist,
            max_length=255,
        )
