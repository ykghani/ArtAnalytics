from typing import Dict, List, Any, Optional, Iterator, Set
from pathlib import Path
from PIL import Image
from io import BytesIO
import logging
from dataclasses import dataclass, field

from requests.sessions import Session as Session

from .base import MuseumAPIClient, MuseumImageProcessor
from ..download.progress_tracker import ProgressState, BaseProgressTracker
from .schemas import ArtworkMetadata, MuseumInfo
from ..utils import sanitize_filename


class MetClient(MuseumAPIClient):
    '''Metropolitan Museum of Art Client Implmentation'''
    
    def __init__(self, museum_info: MuseumInfo, api_key: Optional[str] = None, cache_file: Optional[Path] = None, progress_tracker: Optional[BaseProgressTracker] = None):
        super().__init__(museum_info= museum_info, api_key= api_key, cache_file= cache_file)
        self.progress_tracker = progress_tracker
    
    def _get_auth_header(self) -> str:
        '''Met does not require authentication'''
        return ""
    
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
    
    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        '''Implement ID-based collection iteration for Met with resumption'''
        try:
            # Get all object IDs first
            url = f"{self.museum_info.base_url}/objects"
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
            
            total_ids = data.get('total', 0)
            object_ids = data.get('objectIDs', [])
            
            if not object_ids:
                logging.warning(f"No objects found matching criteria. Total: {total_ids}")
                return
            
            # Find starting point if we have a progress tracker
            start_idx = 0
            if isinstance(self.progress_tracker, MetProgressTracker):
                last_id = self.progress_tracker.state.last_object_id
                if last_id is not None:
                    try:
                        # Find where we left off and add 1 to start with next item
                        start_idx = object_ids.index(int(last_id)) + 1
                        logging.info(f"Resuming from object ID {last_id} (index {start_idx})")
                    except (ValueError, TypeError) as e:
                        logging.warning(f"Could not find last processed ID {last_id}, starting from beginning: {e}")
                        start_idx = 0
            
            # Resume from last position
            remaining_ids = object_ids[start_idx:]
            total_remaining = len(remaining_ids)
            logging.info(f"Processing {total_remaining} objects starting from index {start_idx}")
            
            # Add progress logging
            progress_interval = max(1, total_remaining // 100)  # Log every 1% progress
            
            # Iterate through remaining IDs
            for idx, object_id in enumerate(remaining_ids):
                # Log progress periodically
                if idx % progress_interval == 0:
                    progress = (idx / total_remaining) * 100
                    logging.info(f"Progress: {progress:.1f}% - Processing object {object_id} ({idx}/{total_remaining})")
                
                try:
                    artwork = self._get_artwork_details_impl(str(object_id))
                    if artwork:
                        # Update progress tracker state
                        if isinstance(self.progress_tracker, MetProgressTracker):
                            self.progress_tracker.state.total_objects = total_ids
                            self.progress_tracker.state.last_object_id = str(object_id)
                        yield artwork
                except Exception as e:
                    logging.error(f"Error getting metadata for artwork {object_id}: {e}")
                    continue

        except Exception as e:
            logging.error(f"Error fetching object IDs: {e}")
            raise
                
    def _get_artwork_details_impl(self, object_id: str) -> Optional[ArtworkMetadata]:
        '''Implement artwork details fetching for Met'''
        url = f"{self.museum_info.base_url}/objects/{object_id}"
        
        try:
            logging.debug(f"Fetching details for artwork {object_id}")
            response = self.session.get(url, timeout=(5, 30))
            response.raise_for_status()
            artwork = ArtworkMetadata.from_met_response(response.json())
            logging.debug(f"Successfully fetched artwork {object_id}")
            return artwork
        
        except Exception as e:
            logging.error(f"Error fetching details for artwork {object_id}: {e}")
            raise
    
class MetImageProcessor(MuseumImageProcessor):
    '''Met image processor implementation'''
    
    def process_image(self, image_data: bytes, metadata: ArtworkMetadata) -> Path: 
        '''Process and save artwork image'''
        try:
            image = Image.open(BytesIO(image_data))
            
            filename = self.generate_filename(metadata)
            filepath = self.output_dir / filename
            
            image.save(filepath, format= 'JPEG', quality= 95)
            return filepath
        except Exception as e: 
            raise RuntimeError(f"Failed to process object {metadata.id}: {str(e)}")
        
    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        '''Generate filename for the artwork'''
        return sanitize_filename(
            id= f"Met_{metadata.id}",
            title= metadata.title,
            artist= metadata.artist,
            max_length= 255
        )

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
        self.state = MetProgressState()
        super().__init__(progress_file)
    
    def get_state_dict(self) -> Dict[str, Any]:
        return {
            'processed_ids': list(self.state.processed_ids),
            'success_ids': list(self.state.success_ids),
            'failed_ids': list(self.state.failed_ids),
            'error_log': self.state.error_log,
            'total_objects': self.state.total_objects,
            'last_object_id': self.state.last_object_id
        }
    
    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get('processed_ids', []))
        self.state.success_ids = set(data.get('success_ids', []))
        self.state.failed_ids = set(data.get('failed_ids', []))
        self.state.error_log = data.get('error_log', {})
        self.state.total_objects = data.get('total_objects', 0)
        self.state.last_object_id = data.get('last_object_id')
        