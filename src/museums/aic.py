from typing import Dict, Any, Optional, Iterator, Set
from pathlib import Path
import json
from dataclasses import dataclass, field

from .base import MuseumAPIClient, MuseumImageProcessor
from ..config import settings
from .schemas import ArtworkMetadata, MuseumInfo, AICArtworkFactory
from ..download.progress_tracker import BaseProgressTracker, ProgressState
from ..utils import setup_logging


class AICClient(MuseumAPIClient):
    """Art Institute of Chicago API Client implementation"""

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[BaseProgressTracker] = None,
        data_dump_path: Optional[Path] = None,
    ):
        super().__init__(
            museum_info=museum_info, api_key=api_key, cache_file=cache_file
        )
        self.progress_tracker = progress_tracker
        self.artwork_factory = AICArtworkFactory()
        self.data_dump_path = data_dump_path
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "aic")

    def _get_auth_header(self) -> str:
        if not self.api_key:
            return ""
        return f"Bearer {self.api_key}"

    def get_artwork_page(self, page: int, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch a page of artworks"""
        url = f"{self.museum_info.base_url}"  # base_url already includes /artworks
        params["page"] = page

        self.logger.debug(f"Requesting url: {url} with params: {params}")

        response = self.session.get(url, params=params, timeout=(5, 30))
        response.raise_for_status()
        return response.json()

    def _get_artwork_details_impl(self, artwork_id: str) -> ArtworkMetadata:
        """Implement artwork details fetching for AIC"""
        url = f"{self.museum_info.base_url}/{artwork_id}"

        self.logger.debug(f"Fetching artwork details from: {url}")

        try:
            response = self.session.get(url, timeout=(5, 30))
            response.raise_for_status()
            # return ArtworkMetadata.from_aic_response(response.json()['data'])
            return self.artwork_factory.create_metadata(response.json()["data"])
        except Exception as e:
            self.logger.error(f"Error fetching details for artwork {artwork_id}: {e}")
            raise

    def get_departments(self) -> Dict[str, Any]:
        """Get department listings"""
        url = f"{self.museum_info.base_url}/departments"
        response = self.session.get(url, timeout=(5, 30))
        response.raise_for_status()
        return response.json()

    # def build_image_url(self, image_id: str, **kwargs) -> str:
    #     """Build IIIF image URL"""
    #     size = kwargs.get('size', 'full')
    #     return f"https://www.artic.edu/iiif/2/{image_id}/{size}/0/default.jpg"

    def search_artworks(self, query: str, **kwargs) -> Dict[str, Any]:
        """Search for artworks"""
        url = f"{self.museum_info.base_url}/artworks/search"
        params = {"q": query, **kwargs}
        response = self.session.get(url, params=params, timeout=(5, 30))
        response.raise_for_status()
        return response.json()

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        """Choose data source based on availability"""
        self.logger.debug(f"Data dump path type: {type(self.data_dump_path)}")
        self.logger.debug(f"Data dump path: {self.data_dump_path}")
        self.logger.debug(
            f"Data dump exists: {self.data_dump_path and self.data_dump_path.exists() if self.data_dump_path else False}"
        )

        if self.data_dump_path and self.data_dump_path.exists():
            self.logger.info(f"Using data dump for collection iteration")
            yield from self._iter_data_dump()
        else:
            self.logger.info(f"Using API for collection iteration")
            yield from self._iter_api_collection(**params)

    def _iter_api_collection(self, **params) -> Iterator[ArtworkMetadata]:
        """Iterate through API results with pagination"""
        page = 1
        limit = 100  # AIC's standard page size

        while True:
            try:
                params["limit"] = limit
                data = self.get_artwork_page(page, params)

                if not data.get("data"):
                    break

                for artwork in data["data"]:
                    yield self.artwork_factory.create_metadata(artwork)

                if self.progress_tracker:
                    total_pages = data.get("pagination", {}).get("total_pages", 0)
                    self.progress_tracker.note_page(page, total_pages=total_pages)

                page += 1

            except Exception as e:
                self.logger.error(f"Error fetching page {page}: {e}")
                raise

    def _iter_data_dump(self) -> Iterator[ArtworkMetadata]:
        """Iterate through JSON files in data dump directory"""
        try:
            # Get and sort all JSON files - this creates a stable order
            artwork_files = sorted(list(self.data_dump_path.glob("*.json")))
            total_files = len(artwork_files)

            if self.progress_tracker:
                self.progress_tracker.note_index(0, total=total_files)
                start_idx = self.progress_tracker.state.last_processed_index if hasattr(self.progress_tracker.state, "last_processed_index") else 0
            else:
                start_idx = 0

            self.logger.info(f"Starting from index {start_idx} of {total_files} files")

            # Process files from the last saved index
            for idx, file_path in enumerate(artwork_files[start_idx:], start=start_idx):
                try:
                    with open(file_path) as f:
                        artwork_data = json.load(f)

                    metadata = self.artwork_factory.create_metadata(artwork_data)
                    if metadata and metadata.is_public_domain:
                        if self.progress_tracker:
                            self.progress_tracker.note_index(idx)
                            self.progress_tracker._save_progress()
                        yield metadata

                except Exception as e:
                    self.logger.error(f"Error processing file {file_path}: {e}")
                    continue

        except Exception as e:
            self.logger.error(f"Error reading data dump directory: {e}")
            raise

    def get_collection_info(self) -> Dict[str, Any]:
        """Get basic collection information"""
        url = f"{self.museum_info.base_url}/search"
        params = {"limit": 0}  # Just get total count, no results

        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            return {"total_objects": data.get("pagination", {}).get("total", 0)}
        except Exception as e:
            logging.error(f"Error getting collection info: {e}")
            return {"total_objects": 0}


class AICImageProcessor(MuseumImageProcessor):
    """Art Institute of Chicago image processor implementation"""

    def __init__(self, output_dir: Path, museum_info: MuseumInfo):
        super().__init__(output_dir, museum_info)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "aic")


@dataclass
class AICProgressState(ProgressState):
    last_page: int = 0
    total_pages: int = 0
    processed_ids: Set[str] = field(default_factory=set)
    success_ids: Set[str] = field(default_factory=set)
    failed_ids: Set[str] = field(default_factory=set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory=dict)
    last_processed_index: int = 0  # For data dump processing
    total_files: int = 0


class AICProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        # Initialize state before calling super().__init__() since parent's _load_progress()
        # calls restore_state() which needs self.state to exist
        self.state = AICProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        # Override the parent's logger with museum-specific logger
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "aic")

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "last_page": self.state.last_page,
            "total_pages": self.state.total_pages,
            "last_processed_index": self.state.last_processed_index,
            "total_files": self.state.total_files,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", {})
        self.state.last_page = data.get("last_page", 0)
        self.state.total_pages = data.get("total_pages", 0)
        self.state.last_processed_index = data.get("last_processed_index", 0)
        self.state.total_files = data.get("total_files", 0)

    def note_page(self, page: int, *, total_pages: int = 0) -> None:
        self.state.last_page = page
        if total_pages:
            self.state.total_pages = total_pages

    def note_index(self, idx: int, *, total: int = 0) -> None:
        self.state.last_processed_index = idx
        if total:
            self.state.total_files = total

    def update_page(self, page: int) -> None:
        self.state.last_page = page
        self._save_progress()

    def get_last_page(self) -> int:
        return self.state.last_page
