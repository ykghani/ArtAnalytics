"""National Gallery of Art museum client.

Data source: Two CSV files from https://github.com/NationalGalleryOfArt/opendata
  - objects.csv      — artwork metadata (objectid, title, attribution, …)
  - published_images.csv — image metadata (uuid, depictstmsobjectid, viewtype, …)

Join on: objects.objectid == published_images.depictstmsobjectid
Filter:  published_images.viewtype == "primary"
Image URL: https://api.nga.gov/iiif/{uuid}/full/!2000,2000/0/default.jpg
"""
import csv
import time
from io import BytesIO
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Set

from PIL import Image

from .base import MuseumAPIClient, MuseumImageProcessor
from .schemas import ArtworkMetadata, ArtworkMetadataFactory, MuseumInfo
from ..config import settings
from ..download.progress_tracker import BaseProgressTracker
from ..utils import sanitize_filename, setup_logging

NGA_KEEP_CLASSIFICATIONS = {
    "Painting",
    "Drawing",
    "Print",
    "Photograph",
    "Index of American Design",
}

NGA_OBJECTS_URL = (
    "https://raw.githubusercontent.com/NationalGalleryOfArt/opendata"
    "/main/data/objects.csv"
)
NGA_IMAGES_URL = (
    "https://raw.githubusercontent.com/NationalGalleryOfArt/opendata"
    "/main/data/published_images.csv"
)


def _nga_iiif_url(uuid: str) -> str:
    return f"https://api.nga.gov/iiif/{uuid}/full/!2000,2000/0/default.jpg"


class NGAArtworkFactory(ArtworkMetadataFactory):
    """Factory for National Gallery of Art artwork metadata.

    Input `data` is a merged dict from objects.csv + published_images.csv.
    """

    def __init__(self):
        super().__init__("nga")

    def create_metadata(self, data: Dict[str, Any]) -> Optional[ArtworkMetadata]:
        object_id = data.get("objectid", "").strip()
        if not object_id:
            return None

        uuid = data.get("uuid", "").strip()
        if not uuid:
            return None

        try:
            iiif = _nga_iiif_url(uuid)
            begin = data.get("beginyear", "").strip()
            end = data.get("endyear", "").strip()

            return ArtworkMetadata(
                id=object_id,
                accession_number=data.get("accessionnumber", "").strip(),
                title=data.get("title", "Untitled").strip() or "Untitled",
                artist=data.get("attribution", "Unknown Artist").strip() or "Unknown Artist",
                date_display=data.get("displaydate", "").strip() or None,
                date_start=begin or None,
                date_end=end or None,
                medium=data.get("medium", "").strip() or None,
                dimensions=data.get("dimensions", "").strip() or None,
                department=data.get("subclassification", "").strip() or None,
                artwork_type=data.get("classification", "").strip() or None,
                is_public_domain=True,
                credit_line=data.get("custodian", "").strip() or None,
                primary_image_url=iiif,
                image_urls={"iiif": iiif},
            )
        except Exception as e:
            self.logger.error(f"Error creating NGA metadata for objectid={object_id}: {e}")
            return None


@dataclass
class NGAProgressState:
    processed_ids: Set[str] = field(default_factory=set)
    success_ids: Set[str] = field(default_factory=set)
    failed_ids: Set[str] = field(default_factory=set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory=dict)
    last_index: int = 0
    total_objects: int = 0


class NGAProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        self.state = NGAProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "nga")

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "last_index": self.state.last_index,
            "total_objects": self.state.total_objects,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", {})
        self.state.last_index = data.get("last_index", 0)
        self.state.total_objects = data.get("total_objects", 0)


class NGAClient(MuseumAPIClient):
    """National Gallery of Art API client.

    Downloads CSV data dumps from GitHub, joins them, and iterates artworks.
    CSVs are cached under `data_dump_path` to avoid re-downloading.
    """

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[NGAProgressTracker] = None,
        data_dump_path: Optional[Path] = None,
    ):
        super().__init__(museum_info=museum_info, api_key=api_key, cache_file=cache_file)
        self.progress_tracker = progress_tracker
        self.data_dump_path = data_dump_path
        self.artwork_factory = NGAArtworkFactory()
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "nga")

    def _get_auth_header(self) -> str:
        return ""

    def get_collection_info(self) -> Dict[str, Any]:
        rows = self._load_joined_rows()
        return {"total_objects": len(rows)}

    def _download_csv(self, url: str, local_path: Path) -> None:
        """Download a CSV file to local_path if not already present."""
        if local_path.exists():
            self.logger.info(f"Using cached CSV: {local_path}")
            return
        self.logger.info(f"Downloading {url} -> {local_path}")
        response = self.session.get(url, timeout=120)
        response.raise_for_status()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(response.content)

    def _load_joined_rows(self) -> list:
        """Load objects.csv + published_images.csv, join on objectid, return merged dicts."""
        if hasattr(self, '_joined_rows_cache'):
            return self._joined_rows_cache
        dump_dir = self.data_dump_path or (settings.data_dir / "nga" / "csvs")
        objects_path = dump_dir / "objects.csv"
        images_path = dump_dir / "published_images.csv"

        self._download_csv(NGA_OBJECTS_URL, objects_path)
        self._download_csv(NGA_IMAGES_URL, images_path)

        objects: Dict[str, Dict] = {}
        with open(objects_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                objects[row["objectid"]] = row

        joined: Dict[str, Dict] = {}
        with open(images_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                obj_id = row.get("depictstmsobjectid", "")
                if obj_id not in objects:
                    continue
                obj = objects[obj_id]
                classification = obj.get("classification", "").strip()
                if classification not in NGA_KEEP_CLASSIFICATIONS:
                    continue
                viewtype = row.get("viewtype", "").lower()
                if viewtype == "primary" or obj_id not in joined:
                    if row.get("uuid", "").strip():
                        joined[obj_id] = {**obj, **row}

        self._joined_rows_cache = list(joined.values())
        return self._joined_rows_cache

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        rows = self._load_joined_rows()
        start = 0
        if self.progress_tracker and isinstance(self.progress_tracker, NGAProgressTracker):
            start = self.progress_tracker.state.last_index
            self.progress_tracker.state.total_objects = len(rows)

        self.logger.info(f"NGA: {len(rows)} joined rows, starting at index {start}")

        for idx, row in enumerate(rows[start:], start=start):
            obj_id = row.get("objectid", "")
            if self.progress_tracker and self.progress_tracker.is_processed(obj_id):
                continue
            metadata = self.artwork_factory.create_metadata(row)
            if metadata:
                yield metadata

            if self.progress_tracker and isinstance(self.progress_tracker, NGAProgressTracker):
                self.progress_tracker.state.last_index = idx
                if idx % 500 == 0:
                    self.progress_tracker.force_save()

            time.sleep(self.museum_info.rate_limit)

    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        return None


class NGAImageProcessor(MuseumImageProcessor):
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
            raise RuntimeError(f"Failed to process NGA artwork {metadata.id}: {e}")

    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        return sanitize_filename(
            id=f"NGA_{metadata.id}",
            title=metadata.title,
            artist=metadata.artist,
            max_length=255,
        )
