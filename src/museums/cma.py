from typing import Dict, List, Any, Optional, Iterator, Set
import requests
from pathlib import Path
import logging
from dataclasses import dataclass, field

from .base import MuseumAPIClient, MuseumImageProcessor
from ..config import settings
from ..download.progress_tracker import BaseProgressTracker, ProgressState
from .schemas import ArtworkMetadata, MuseumInfo, CMAArtworkFactory
from ..utils import setup_logging


class CMAClient(MuseumAPIClient):  # Renamed from ClevelandClient
    """Cleveland Museum of Art API Client Implementation"""

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
        self.artwork_factory = CMAArtworkFactory()
        self.object_ids_cache_file = (
            Path(cache_file).parent / "cma_object_ids_cache.json"
            if cache_file
            else None
        )
        self.data_dump_path = data_dump_path
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "cma")

    def _get_auth_header(self) -> str:
        """Cleveland does not require authentication"""
        return ""

    def get_total_objects(self) -> int:
        """Get total number of objects in collection"""
        self.logger.debug("Fetching total object count")
        url = f"{self.museum_info.base_url}/artworks/"
        response = self.session.get(url)
        response.raise_for_status()
        total = response.json().get("info", {}).get("total", 0)
        self.logger.progress(f"Total objects in collection: {total}")
        return total

    def get_collection_info(self) -> Dict[str, Any]:
        """Get basic collection information"""
        return {"total_objects": self.get_total_objects()}

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
            self.logger.info(f"Using default iterator from API")
            yield from self._iter_api_collection_impl(**params)

    def _iter_data_dump(self) -> Iterator[ArtworkMetadata]:
        """
        Iterate through data dump file using streaming parser.

        Uses ijson for incremental parsing to avoid loading 295MB file into memory.
        """
        try:
            import ijson
        except ImportError:
            self.logger.error(
                "ijson not installed. Install with: pip install ijson\n"
                "Falling back to standard json.load (high memory usage)"
            )
            # Fallback to old method if ijson not available
            with open(self.data_dump_path) as f:
                artworks = json.load(f)

            start_index = getattr(getattr(self.progress_tracker, "state", None), "last_processed_index", 0)
            if self.progress_tracker:
                self.progress_tracker.note_total(len(artworks))
                self.logger.info(
                    f"Starting from index {start_index} of {len(artworks)} total objects"
                )

            for idx, artwork in enumerate(artworks[start_index:], start=start_index):
                try:
                    metadata = self.artwork_factory.create_metadata(artwork)
                    if metadata and metadata.is_public_domain:
                        if self.progress_tracker:
                            self.progress_tracker.note_index(idx)
                        yield metadata
                except Exception as e:
                    self.logger.error(f"Error processing artwork at index {idx}: {e}")
            return

        # Stream JSON using ijson (memory-efficient)
        try:
            start_index = getattr(getattr(self.progress_tracker, "state", None), "last_processed_index", 0)

            self.logger.info(f"Streaming JSON from {self.data_dump_path} (starting at index {start_index})")

            with open(self.data_dump_path, 'rb') as f:
                # Stream array items from root level
                idx = 0
                for artwork in ijson.items(f, 'item'):
                    if idx < start_index:
                        idx += 1
                        continue

                    try:
                        metadata = self.artwork_factory.create_metadata(artwork)
                        if metadata and metadata.is_public_domain:
                            if self.progress_tracker:
                                self.progress_tracker.note_index(idx)
                            yield metadata
                    except Exception as e:
                        self.logger.error(
                            f"Error processing artwork {artwork.get('id', 'unknown')} at index {idx}: {e}"
                        )
                        continue
                    finally:
                        idx += 1

        except Exception as e:
            self.logger.error(f"Error reading data dump: {e}")
            raise

    def _iter_api_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        """Iterate through CMA collection objects"""
        try:
            # First get all artwork IDs
            artwork_ids = self._get_artwork_ids(**params)
            self.logger.progress(f"Retrieved {len(artwork_ids)} total artwork IDs")

            if not artwork_ids:
                self.logger.progress("No artworks found matching criteria")
                return

            # Filter out already processed IDs
            unprocessed_ids = self._get_unprocessed_ids(artwork_ids)
            total_remaining = len(unprocessed_ids)

            if total_remaining == 0:
                self.logger.progress("All items have been processed.")
                return

            self.logger.progress(
                f"Found {total_remaining} unprocessed artworks out of {len(artwork_ids)} total"
            )

            progress_interval = max(1, total_remaining // 100)
            for idx, artwork_id in enumerate(unprocessed_ids):
                if idx % progress_interval == 0:
                    progress = (idx / total_remaining) * 100
                    self.logger.progress(
                        f"Progress: {progress:.1f}% ({idx}/{total_remaining})"
                    )

                try:
                    artwork = self._get_artwork_details_impl(str(artwork_id))
                    if artwork:
                        if self.progress_tracker:
                            self.progress_tracker.note_total(len(artwork_ids))
                        self.logger.artwork(
                            f"Successfully processed artwork {artwork_id}"
                        )
                        yield artwork
                except Exception as e:
                    self.logger.error(f"Error processing artwork {artwork_id}: {e}")
                    continue

        except Exception as e:
            self.logger.error(f"Error in collection iteration: {e}")
            raise

    def _get_artwork_ids(self, **params) -> List[int]:
        """Get list of artwork IDs matching search parameters"""
        all_ids = self._load_cached_ids(self.object_ids_cache_file)
        if all_ids is not None:
            self.logger.progress(f"List of artwork ids loaded from cache")
            return all_ids

        all_ids = []
        skip = 0
        limit = 1000
        total = None

        params["fields"] = "id"
        self.logger.debug(f"Fetching artwork IDs with params: {params}")

        try:
            while True:
                page_params = {**params, "skip": skip, "limit": limit}
                response = self.session.get(
                    f"{self.museum_info.base_url}/artworks/", params=page_params
                )
                response.raise_for_status()
                data = response.json()

                if total is None:
                    total = data.get("info", {}).get("total", 0)

                artworks = data.get("data", [])
                if not artworks:
                    break

                artwork_ids = [art["id"] for art in artworks]  # Get actual IDs
                all_ids.extend(artwork_ids)

                self.logger.progress(f"Retrieved {len(all_ids)}/{total} artwork IDs")

                skip += limit
                if skip >= total:
                    break

            if all_ids:
                self._save_cached_ids(self.object_ids_cache_file, all_ids)
            return all_ids

        except requests.RequestException as e:
            self.logger.error(f"Error fetching artwork IDs: {e}")
            raise


    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        """Implement artwork details fetching for Cleveland"""
        url = f"{self.museum_info.base_url}/artworks/{artwork_id}"

        try:
            self.logger.debug(f"Fetching details from: {url}")
            response = self.session.get(url, timeout=(5, 30))
            response.raise_for_status()
            artwork = response.json().get("data", {})
            return self.artwork_factory.create_metadata(artwork)

        except Exception as e:
            self.logger.error(f"Error fetching details for artwork {artwork_id}: {e}")
            raise


class CMAImageProcessor(MuseumImageProcessor):
    """Cleveland Museum of Art image processor implementation"""

    def __init__(self, output_dir: Path, museum_info: MuseumInfo):
        super().__init__(output_dir, museum_info)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "cma")


@dataclass
class CMAProgressState(ProgressState):
    """CMA progress state — inherits 4 base fields, adds CMA-specific resume fields."""

    total_objects: int = 0
    last_processed_index: int = 0


class CMAProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        # Initialize state before calling super().__init__() since parent's _load_progress()
        # calls restore_state() which needs self.state to exist
        self.state = CMAProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        # Override the parent's logger with museum-specific logger
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "cma")

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "total_objects": self.state.total_objects,
            "last_processed_index": self.state.last_processed_index,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", {})
        self.state.total_objects = data.get("total_objects", 0)
        self.state.last_processed_index = data.get("last_processed_index", 0)
        self.logger.debug(
            f"Restored state with {len(self.state.processed_ids)} processed items"
        )

    def note_index(self, idx: int, *, total: int = 0) -> None:
        self.state.last_processed_index = idx
        if total:
            self.state.total_objects = total

    def note_total(self, total: int) -> None:
        self.state.total_objects = total
