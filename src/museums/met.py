from typing import Dict, List, Any, Optional, Iterator, Set
import time
from pathlib import Path
from PIL import Image
from io import BytesIO
import logging
import json
from dataclasses import dataclass, field
from functools import partial
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from requests.sessions import Session as Session

from .base import MuseumAPIClient, MuseumImageProcessor
from ..config import settings, log_level
from ..download.progress_tracker import ProgressState, BaseProgressTracker
from .schemas import ArtworkMetadata, MuseumInfo, MetArtworkFactory
from ..utils import sanitize_filename, setup_logging


class MetClient(MuseumAPIClient):
    '''Metropolitan Museum of Art Client Implmentation'''
    
    def __init__(self, museum_info: MuseumInfo, api_key: Optional[str] = None, cache_file: Optional[Path] = None, progress_tracker: Optional[BaseProgressTracker] = None):
        super().__init__(museum_info= museum_info, api_key= api_key, cache_file= cache_file)
        self.progress_tracker = progress_tracker
        self.object_ids_cache_file = Path(cache_file).parent / 'object_ids_cache.json' if cache_file else None
        self.artwork_factory = MetArtworkFactory()
        self.logger = setup_logging(settings.logs_dir, log_level, 'met')
    
    def _get_auth_header(self) -> str:
        '''Met does not require authentication'''
        return ""

    def _get_session(self) -> requests.Session: 
        self.logger = setup_logging(settings.logs_dir, log_level, 'met')
        self.logger.debug("Creating Met-specific session with custom retry strategy")
        session = super()._create_session()
        
        #Met specific retry strategy with longer timeouts
        retry_strategy = Retry(
            total=10,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        session.request = partial(session.request, timeout=(30, 300))
        self.logger.debug("Met session configured with custom timeouts and retry strategy")
        return session
    
    def get_total_objects(self) -> int:
        '''Get total number of objects in collection'''
        url = f"{self.museum_info.base_url}/objects"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json().get('total', 0)
    
    def get_collection_info(self) -> Dict[str, Any]:
        return {
            'total_objects': self.get_total_objects()
        }
    
    def get_image(self, image_url: str) -> bytes: 
        '''Download image directly from Met's url'''
        if not image_url: 
            raise ValueError("No image URL provided")
    
    def _get_object_ids(self, **params) -> List[int]:
        """Get list of object IDs matching search parameters"""
        # Try to load from cache first
        self.logger.debug(f"Attempting to fetch object IDs with params: {params}")
        cached_ids = self._load_cached_object_ids()
        if cached_ids is not None:
            self.logger.progress(f"Using cached object IDs ({len(cached_ids)} objects)")
            return cached_ids
        
        # If no cache, fetch from API
        objects = []
        department_ids = params.get('departmentIds', '').split('|')
        self.logger.debug(f"Fetching objects for {len(department_ids)} departments")
        
        for dept_id in department_ids:
            search_params = {**params, 'departmentIds': dept_id}
            max_retries = 5
            retry_delay = 2
            
            for attempt in range(max_retries):
                try:
                    url = f"{self.museum_info.base_url}/objects"
                    self.logger.debug(f"Fetching department {dept_id}, attempt {attempt + 1}/{max_retries}")
                    response = self.session.get(url, params=search_params)
                    response.raise_for_status()
                    data = response.json()
                    new_objects = data.get('objectIDs', [])
                    objects.extend(new_objects)
                    self.logger.progress(f"Retrieved {len(new_objects)} objects from department {dept_id}")
                    break
                except Exception as e:
                    if attempt == max_retries - 1:
                        self.logger.error(f"Failed to fetch objects for department {dept_id} after {max_retries} attempts: {e}")
                        raise
                    wait_time = retry_delay * (2 ** attempt)
                    self.logger.warning(f"Retrying department {dept_id} in {wait_time}s... ({str(e)})")
                    time.sleep(wait_time)

        # Save to cache before returning
        self._save_object_ids_cache(objects)
        self.logger.progress(f"Successfully fetched and cached {len(objects)} total object IDs")
        return objects

    def _get_unprocessed_ids(self, object_ids: List[int]) -> List[int]:
        """Filter out already processed IDs"""
        if not self.progress_tracker:
            return object_ids
            
        # Convert all IDs to strings for consistent comparison
        str_ids = set(str(id) for id in object_ids)
        processed_ids = self.progress_tracker.state.processed_ids
        
        # Get unprocessed IDs
        unprocessed_ids = str_ids - processed_ids
        
        # Convert back to integers and sort
        return sorted(int(id) for id in unprocessed_ids)

    def _load_cached_object_ids(self) -> Optional[List[int]]:
        """Load object IDs from cache file if it exists and is recent"""
        if not self.object_ids_cache_file or not self.object_ids_cache_file.exists():
            self.logger.debug("No cache file found or specified")
            return None
            
        try:
            cache_stat = self.object_ids_cache_file.stat()
            cache_age = time.time() - cache_stat.st_mtime
            
            # Cache expires after 24 hours
            if cache_age > 60 * 60 * 24:  # 24 hours in seconds
                self.logger.debug("Cache file expired (older than 24 hours)")
                return None
                
            with self.object_ids_cache_file.open('r') as f:
                cached_ids = json.load(f)
                self.logger.debug(f"Successfully loaded {len(cached_ids)} IDs from cache")
                return cached_ids
        except Exception as e:
            self.logger.warning(f"Failed to load object IDs cache: {str(e)}")
            return None
    
    def _save_object_ids_cache(self, object_ids: List[int]) -> None:
        """Save object IDs to cache file"""
        if not self.object_ids_cache_file:
            self.logger.debug("No cache file specified, skipping save")
            return
            
        try:
            with self.object_ids_cache_file.open('w') as f:
                json.dump(object_ids, f)
                self.logger.debug(f"Successfully cached {len(object_ids)} object IDs")
        except Exception as e:
            self.logger.warning(f"Failed to save object IDs cache: {str(e)}")
    
    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        """Iterate through collection objects"""
        try:
            object_ids = self._get_object_ids(**params)
            self.logger.progress(f"Retrieved {len(object_ids)} total object IDs")
            
            if not object_ids:
                self.logger.warning("No objects found matching criteria")
                return
            
            unprocessed_ids = self._get_unprocessed_ids(object_ids)
            total_remaining = len(unprocessed_ids)
            
            if total_remaining == 0: 
                self.logger.progress("All items have been processed")
                return
            
            self.logger.progress(f"Found {total_remaining} unprocessed objects out of {len(object_ids)} total")
            
            progress_interval = max(1, total_remaining // 100)
            
            for idx, object_id in enumerate(unprocessed_ids):
                if idx % progress_interval == 0:
                    progress = (idx / total_remaining) * 100
                    self.logger.progress(f"Progress: {progress:.1f}% ({idx}/{total_remaining})")
                    
                try:
                    artwork = self._get_artwork_details_impl(str(object_id))
                    if artwork:
                        if isinstance(self.progress_tracker, MetProgressTracker):
                            self.progress_tracker.state.total_objects = len(object_ids)
                            self.progress_tracker.state.last_object_id = str(object_id)
                        self.logger.artwork(f"Successfully processed artwork {object_id}")
                        yield artwork
                except Exception as e: 
                    self.logger.error(f"Error processing artwork {object_id}: {str(e)}")
                    continue
            
        except Exception as e:
            self.logger.error(f"Fatal error in collection iteration: {str(e)}")
            raise  
                
    def _get_artwork_details_impl(self, object_id: str) -> Optional[ArtworkMetadata]:
        '''Implement artwork details fetching for Met'''
        url = f"{self.museum_info.base_url}/objects/{object_id}"
        
        try:
            self.logger.debug(f"Fetching details from: {url}")
            response = self.session.get(url, timeout=(5, 30))
            response.raise_for_status()
            data = response.json()
            
            if not data: 
                self.logger.warning(f"No data returned for artwork: {object_id}")
                return None
            
            artwork = self.artwork_factory.create_metadata(data)
            if artwork is None:
                self.logger.warning(f'Could not create metadata for artwork: {object_id}')
                return None
            
            self.logger.artwork(f"Successfully fetched artwork {object_id}")
            return artwork
        
        except Exception as e:
            self.logger.error(f"Error fetching details for artwork {object_id}: {str(e)}")
            raise
    
class MetImageProcessor(MuseumImageProcessor):
    '''Met image processor implementation'''
    
    def __init__(self, output_dir: Path, museum_info: MuseumInfo):
        super().__init__(output_dir, museum_info)
        self.logger = setup_logging(settings.logs_dir, log_level, 'met')
    
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
            self.logger.error(f"Failed to process image for artwork {metadata.id}: {str(e)}")
            raise RuntimeError(f"Failed to process object {metadata.id}: {str(e)}")
        
    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        '''Generate filename for the artwork'''
        self.logger.debug(f"Generating filename for artwork {metadata.id}")
        filename = sanitize_filename(
            id=f"Met_{metadata.id}",
            title=metadata.title,
            artist=metadata.artist,
            max_length=255
        )
        self.logger.debug(f"Generated filename: {filename}")
        return filename

@dataclass 
class MetProgressState(ProgressState):
    total_objects: int = 0
    last_object_id: Optional[str] = None
    
    processed_ids: Set[str] = field(default_factory= set)
    success_ids: Set[str] = field(default_factory= set)
    failed_ids: Set[str] = field(default_factory= set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory= dict)

class MetProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path):
        self.logger = setup_logging(settings.logs_dir, log_level, 'met')
        self.logger.debug(f"Initializing Met progress tracker with file: {progress_file}")
        self.state = MetProgressState()
        super().__init__(progress_file)
    
    def get_state_dict(self) -> Dict[str, Any]:
        self.logger.debug("Preparing progress state dictionary")
        state_dict = {
            'processed_ids': list(self.state.processed_ids),
            'success_ids': list(self.state.success_ids),
            'failed_ids': list(self.state.failed_ids),
            'error_log': self.state.error_log,
            'total_objects': self.state.total_objects,
            'last_object_id': self.state.last_object_id
        }
        self.logger.debug(f"Current state: {len(state_dict['processed_ids'])} processed, "
                         f"{len(state_dict['success_ids'])} successful, "
                         f"{len(state_dict['failed_ids'])} failed")
        return state_dict
    
    def restore_state(self, data: Dict[str, Any]) -> None:
        self.logger.debug("Restoring progress tracker state")
        self.state.processed_ids = set(data.get('processed_ids', []))
        self.state.success_ids = set(data.get('success_ids', []))
        self.state.failed_ids = set(data.get('failed_ids', []))
        self.state.error_log = data.get('error_log', {})
        self.state.total_objects = data.get('total_objects', 0)
        self.state.last_object_id = data.get('last_object_id')
        
        self.logger.progress(f"Restored state with {len(self.state.processed_ids)} processed items, "
                           f"{len(self.state.success_ids)} successful, "
                           f"{len(self.state.failed_ids)} failed")