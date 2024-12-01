from typing import Dict, List, Any, Optional, Iterator, Set
import requests
from pathlib import Path
from PIL import Image
from io import BytesIO
import logging
from dataclasses import dataclass, field
import time
import json

from .base import MuseumAPIClient, MuseumImageProcessor
from ..config import settings, log_level
from ..download.progress_tracker import BaseProgressTracker
from .schemas import ArtworkMetadata, MuseumInfo, CMAArtworkFactory
from ..utils import sanitize_filename, setup_logging

class CMAClient(MuseumAPIClient):  # Renamed from ClevelandClient
    '''Cleveland Museum of Art API Client Implementation'''
    
    def __init__(self, museum_info: MuseumInfo, api_key: Optional[str] = None, 
                 cache_file: Optional[Path] = None, 
                 progress_tracker: Optional[BaseProgressTracker] = None):
        super().__init__(museum_info=museum_info, api_key=api_key, cache_file=cache_file)
        self.progress_tracker = progress_tracker
        self.artwork_factory = CMAArtworkFactory()
        self.object_ids_cache_file = Path(cache_file).parent / 'cma_object_ids_cache.json' if cache_file else None
        self.logger = setup_logging(settings.logs_dir, log_level, 'cma')
    
    def _get_auth_header(self) -> str:
        '''Cleveland does not require authentication'''
        return ""

    def get_total_objects(self) -> int:
        '''Get total number of objects in collection'''
        self.logger.debug("Fetching total object count")
        url = f"{self.museum_info.base_url}/artworks/"
        response = self.session.get(url)
        response.raise_for_status()
        total = response.json().get('info', {}).get('total', 0)
        self.logger.progress(f"Total objects in collection: {total}")
        return total
    
    def get_collection_info(self) -> Dict[str, Any]:
        '''Get basic collection information'''
        return {
            'total_objects': self.get_total_objects()
        }

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
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
                
            self.logger.progress(f"Found {total_remaining} unprocessed artworks out of {len(artwork_ids)} total")
            
            
            progress_interval = max(1, total_remaining // 100)
            for idx, artwork_id in enumerate(unprocessed_ids):
                if idx % progress_interval == 0:
                    progress = (idx / total_remaining) * 100
                    self.logger.progress(f"Progress: {progress:.1f}% ({idx}/{total_remaining})")
                    
                try:
                    artwork = self._get_artwork_details_impl(str(artwork_id))
                    if artwork:
                        if isinstance(self.progress_tracker, CMAProgressTracker):
                            self.progress_tracker.state.total_objects = len(artwork_ids)
                            self.progress_tracker.state.last_object_id = str(artwork_id)
                        self.logger.artwork(f"Successfully processed artwork {artwork_id}")
                        yield artwork
                except Exception as e:
                    self.logger.error(f"Error processing artwork {artwork_id}: {e}")
                    continue
                    
        except Exception as e:
            self.logger.error(f"Error in collection iteration: {e}")
            raise

    def _get_artwork_ids(self, **params) -> List[int]:
        """Get list of artwork IDs matching search parameters"""
        
        all_ids = []
        skip = 0
        limit = 1000
        total = 0
        
        params['fields'] = 'id'
        self.logger.debug(f"Fetching artwork IDs with params: {params}")
        
        try:
            while True:
                page_params = {**params, 'skip': skip, 'limit': limit}
                response = self.session.get(f"{self.museum_info.base_url}/artworks/", params=page_params)
                response.raise_for_status()
                data = response.json()
                
                if skip == 0:
                    total = data.get('info', {}).get('total', 0)
                    self.logger.debug(f"Total available artworks: {total}")
                
                artworks = data.get('data', [])
                if not artworks:
                    break
                    
                all_ids.extend(art['id'] for art in artworks)
                self.logger.progress(f"Retrieved {len(all_ids)}/{total} artwork IDs")
                
                skip += limit
                if skip >= total:
                    break
                    
        except requests.RequestException as e:
            self.logger.error(f'Error fetching artwork IDs: {e}')
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response: {e.response.status_code} - {e.response.text[:500]}...")
            raise
        
        except Exception as e:
            self.logger.error(f'Unexpected error fetching artwork IDs: {e}')
            raise
        
        return all_ids

    def _get_unprocessed_ids(self, artwork_ids: List[int]) -> List[int]:
        """Filter out already processed IDs"""
        if not self.progress_tracker:
            return artwork_ids
            
        str_ids = set(str(id) for id in artwork_ids)
        processed_ids = self.progress_tracker.state.processed_ids
        unprocessed_ids = str_ids - processed_ids
        
        return sorted(int(id) for id in unprocessed_ids)

    def _load_cached_object_ids(self) -> Optional[List[int]]:
        """Load artwork IDs from cache file if it exists and is recent"""
        if not self.object_ids_cache_file or not self.object_ids_cache_file.exists():
            return None
            
        try:
            cache_stat = self.object_ids_cache_file.stat()
            cache_age = time.time() - cache_stat.st_mtime
            
            # Cache expires after 24 hours
            if cache_age > 60 * 60 * 24:
                return None
                
            with self.object_ids_cache_file.open('r') as f:
                cached_ids = json.load(f)
                # Validate cache - don't use if empty
                if not cached_ids:
                    logging.warning("Cached ID list is empty, fetching fresh data")
                    return None
                return cached_ids
        except Exception as e:
            logging.warning(f"Failed to load artwork IDs cache: {e}")
            # Delete invalid cache file
            try:
                self.object_ids_cache_file.unlink(missing_ok=True)
            except Exception as del_e:
                logging.warning(f"Failed to delete invalid cache file: {del_e}")
            return None

    def _save_object_ids_cache(self, artwork_ids: List[int]) -> None:
        """Save artwork IDs to cache file"""
        if not self.object_ids_cache_file:
            return
            
        try:
            with self.object_ids_cache_file.open('w') as f:
                json.dump(artwork_ids, f)
        except Exception as e:
            logging.warning(f"Failed to save artwork IDs cache: {e}")
    
    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        '''Implement artwork details fetching for Cleveland'''
        url = f"{self.museum_info.base_url}/artworks/{artwork_id}"
        
        try:
            self.logger.debug(f"Fetching details from: {url}")
            response = self.session.get(url, timeout=(5, 30))
            response.raise_for_status()
            artwork = response.json().get('data', {})
            return self.artwork_factory.create_metadata(artwork)
            
        except Exception as e:
            self.logger.error(f"Error fetching details for artwork {artwork_id}: {e}")
            raise

class CMAImageProcessor(MuseumImageProcessor):  
    '''Cleveland Museum of Art image processor implementation'''
    def __init__(self, output_dir: Path, museum_info: MuseumInfo):
        super().__init__(output_dir, museum_info)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, 'cma')
    
    def process_image(self, image_data: bytes, metadata: ArtworkMetadata) -> Path:
        '''Process and save artwork image'''
        try:
            self.logger.debug(f"Processing image for artwork {metadata.id}")
            image = Image.open(BytesIO(image_data))
            
            filename = self.generate_filename(metadata)
            filepath = self.output_dir / filename
            
            image.save(filepath, format='JPEG', quality=95)
            self.logger.artwork(f"Saved image to {filepath}")
            return filepath
        except Exception as e:
            self.logger.error(f"Failed to process object {metadata.id}: {str(e)}")
            raise RuntimeError(f"Failed to process object {metadata.id}: {str(e)}")
    
    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        '''Generate filename for the artwork'''
        return sanitize_filename(
            id=f"CMA_{metadata.id}",
            title=metadata.title,
            artist=metadata.artist,
            max_length=255
        )

@dataclass
class CMAProgressState:
    """Separate state class for CMA progress tracking"""
    def __init__(self):
        self.processed_ids: Set[str] = set()
        self.success_ids: Set[str] = set()
        self.failed_ids: Set[str] = set()
        self.error_log: Dict[str, Dict[str, str]] = {}
        self.total_objects: int = 0

class CMAProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path):
        self.progress_file = progress_file
        self.state = CMAProgressState()
        self.logger = setup_logging(settings.logs_dir, log_level, 'cma')
        self._load_progress()
    
    def get_state_dict(self) -> Dict[str, Any]:
        return {
            'processed_ids': list(self.state.processed_ids),
            'success_ids': list(self.state.success_ids),
            'failed_ids': list(self.state.failed_ids),
            'error_log': self.state.error_log,
            'total_objects': self.state.total_objects
        }
    
    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get('processed_ids', []))
        self.state.success_ids = set(data.get('success_ids', []))
        self.state.failed_ids = set(data.get('failed_ids', []))
        self.state.error_log = data.get('error_log', {})
        self.state.total_objects = data.get('total_objects', 0)
        self.logger.debug(f"Restored state with {len(self.state.processed_ids)} processed items")