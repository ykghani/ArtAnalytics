from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Iterator, TYPE_CHECKING
import json
import logging
import time
from io import BytesIO
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PIL import Image

from .schemas import ArtworkMetadata, MuseumInfo
from ..utils import sanitize_filename


class MuseumAPIClient(ABC):
    """Abstract base class for museum API clients"""

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
    ):
        self.museum_info = museum_info
        self.api_key = api_key
        self._cache_file = cache_file
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Create a configured requests session with retry logic."""
        if self._cache_file:
            import requests_cache
            session = requests_cache.CachedSession(str(self._cache_file), backend="sqlite")
        else:
            session = requests.Session()

        headers = {}
        if self.museum_info.user_agent:
            headers["User-Agent"] = self.museum_info.user_agent
        if self.api_key:
            headers["Authorization"] = self._get_auth_header()
        session.headers.update(headers)

        retry_strategy = Retry(
            total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        self._customize_session(session)
        return session

    def _customize_session(self, session: requests.Session) -> None:
        """Hook for museum-specific session configuration. Called after base setup."""
        pass

    def _get_unprocessed_ids(self, object_ids: List[int]) -> List[int]:
        """Filter already-processed IDs using self.progress_tracker."""
        tracker = getattr(self, "progress_tracker", None)
        if not tracker:
            return object_ids
        str_ids = {str(oid) for oid in object_ids}
        unprocessed = str_ids - tracker.state.processed_ids
        return sorted(int(oid) for oid in unprocessed)

    def _load_cached_ids(self, cache_file: Path, max_age_hours: int = 24) -> Optional[List[int]]:
        """Load IDs from a JSON cache file if it exists and has not expired."""
        if not cache_file or not cache_file.exists():
            return None
        try:
            age = time.time() - cache_file.stat().st_mtime
            if age > max_age_hours * 3600:
                return None
            with cache_file.open("r") as f:
                return json.load(f)
        except Exception:
            try:
                cache_file.unlink(missing_ok=True)
            except Exception:
                pass
            return None

    def _save_cached_ids(self, cache_file: Path, ids: List[int]) -> None:
        """Persist IDs to a JSON cache file."""
        if not cache_file:
            return
        try:
            with cache_file.open("w") as f:
                json.dump(ids, f)
        except Exception:
            pass

    @abstractmethod
    def _get_auth_header(self) -> str:
        """Return authentication header value"""
        pass

    def iter_collection(self, **params) -> Iterator[ArtworkMetadata]:
        """
        Main interface for iterating through a museum's collection.
        Each museum client implements its own _iter_collection_impl method.
        """
        try:
            yield from self._iter_collection_impl(**params)
        except Exception as e:
            logging.error(f"Error iterating through collection: {e}")
            return

    @abstractmethod
    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        """Implementation specific to each museum API"""
        pass

    @abstractmethod
    def get_collection_info(self) -> Dict[str, Any]:
        pass

    def get_artwork_details(self, artwork_id: str) -> ArtworkMetadata:
        """
        Get detailed info for a specific artwork.
        This could be overridden if needed but provides a common implementation.
        """
        try:
            return self._get_artwork_details_impl(artwork_id)
        except Exception as e:
            logging.error(f"Error fetching artwork {artwork_id}: {e}")
            raise

    @abstractmethod
    def _get_artwork_details_impl(self, artwork_id: str) -> ArtworkMetadata:
        """Implementation specific to each museum API"""
        pass


class MuseumImageProcessor:
    """Base class for processing museum images. Concrete by default — override only what differs."""

    def __init__(self, output_dir: Path, museum_info: MuseumInfo):
        self.output_dir = output_dir
        self.museum_info = museum_info
        self.logger = logging.getLogger(f"museum.{museum_info.code}")
        self._ensure_output_dir()

    def _ensure_output_dir(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _filename_prefix(self) -> str:
        """ID prefix for generated filenames. Override for non-uppercase variants."""
        return self.museum_info.code.upper()

    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        return sanitize_filename(
            id=f"{self._filename_prefix}_{metadata.id}",
            title=metadata.title,
            artist=metadata.artist,
            max_length=255,
        )

    def process_image(self, image_data: bytes, metadata: ArtworkMetadata) -> tuple[Path, int, int]:
        """Open, convert to RGB if needed, save as JPEG q95. Returns (path, width, height)."""
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
            raise RuntimeError(
                f"Failed to process {self.museum_info.code} artwork {metadata.id}: {e}"
            )
