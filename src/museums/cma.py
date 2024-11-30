from typing import Dict, List, Any, Optional, Iterator, Set
from pathlib import Path
from PIL import Image
from io import BytesIO
import logging
from dataclasses import dataclass, field

from .base import MuseumAPIClient, MuseumImageProcessor
from ..download.progress_tracker import BaseProgressTracker
from .schemas import ArtworkMetadata, MuseumInfo, CMAArtworkFactory
from ..utils import sanitize_filename

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

class CMAClient(MuseumAPIClient):  # Renamed from ClevelandClient
    '''Cleveland Museum of Art API Client Implementation'''
    
    def __init__(self, museum_info: MuseumInfo, api_key: Optional[str] = None, 
                 cache_file: Optional[Path] = None, 
                 progress_tracker: Optional[BaseProgressTracker] = None):
        super().__init__(museum_info=museum_info, api_key=api_key, cache_file=cache_file)
        self.progress_tracker = progress_tracker
        self.artwork_factory = CMAArtworkFactory()
    
    def _get_auth_header(self) -> str:
        '''Cleveland does not require authentication'''
        return ""

    def get_total_objects(self) -> int:
        '''Get total number of objects in collection'''
        url = f"{self.museum_info.base_url}/artworks/"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json().get('info', {}).get('total', 0)
    
    def get_collection_info(self) -> Dict[str, Any]:
        '''Get basic collection information'''
        return {
            'total_objects': self.get_total_objects()
        }

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        """Iterate through CMA collection objects"""
        try:
            url = f"{self.museum_info.base_url}/artworks/"
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            artworks = data.get('data', [])
            total_objects = data.get('info', {}).get('total', 0)
            
            if not artworks:
                logging.warning("No objects found matching criteria")
                return
                
            logging.info(f"Retrieved {len(artworks)} objects")
                
            for artwork in artworks:
                try:
                    metadata = self.artwork_factory.create_metadata(artwork)
                    if metadata and isinstance(self.progress_tracker, CMAProgressTracker):
                        self.progress_tracker.state.total_objects = total_objects
                    yield metadata
                except Exception as e:
                    logging.error(f"Error processing artwork {artwork.get('id')}: {e}")
                    continue
                    
        except Exception as e:
            logging.error(f"Error in collection iteration: {e}")
            raise
    
    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        '''Implement artwork details fetching for Cleveland'''
        url = f"{self.museum_info.base_url}/artworks/{artwork_id}"
        
        try:
            response = self.session.get(url, timeout=(5, 30))
            response.raise_for_status()
            artwork = response.json().get('data', {})
            # return self._convert_to_metadata(artwork)
            return self.artwork_factory.create_metadata(artwork)
            
        except Exception as e:
            logging.error(f"Error fetching details for artwork {artwork_id}: {e}")
            raise

class CMAImageProcessor(MuseumImageProcessor):  # Renamed from ClevelandImageProcessor
    '''Cleveland Museum of Art image processor implementation'''
    
    def process_image(self, image_data: bytes, metadata: ArtworkMetadata) -> Path:
        '''Process and save artwork image'''
        try:
            image = Image.open(BytesIO(image_data))
            
            filename = self.generate_filename(metadata)
            filepath = self.output_dir / filename
            
            image.save(filepath, format='JPEG', quality=95)
            return filepath
        except Exception as e:
            raise RuntimeError(f"Failed to process object {metadata.id}: {str(e)}")
    
    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        '''Generate filename for the artwork'''
        return sanitize_filename(
            id=f"CMA_{metadata.id}",
            title=metadata.title,
            artist=metadata.artist,
            max_length=255
        )