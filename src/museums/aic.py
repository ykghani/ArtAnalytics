from typing import Dict, Any, Optional, Iterator
from pathlib import Path
from PIL import Image
import logging
from io import BytesIO
from dataclasses import dataclass

from .base import MuseumAPIClient, MuseumImageProcessor
from .schemas import ArtworkMetadata, MuseumInfo
from ..download.progress_tracker import BaseProgressTracker, ProgressState
from ..utils import sanitize_filename

class AICClient(MuseumAPIClient):
    """Art Institute of Chicago API Client implementation"""
    
    def __init__(self, museum_info: MuseumInfo, api_key: Optional[str] = None, 
                 cache_file: Optional[Path] = None, progress_tracker: Optional[BaseProgressTracker] = None):
        super().__init__(museum_info=museum_info, api_key=api_key, cache_file=cache_file)
        self.progress_tracker = progress_tracker
    
    def _get_auth_header(self) -> str:
        if not self.api_key:
            return ""
        return f"Bearer {self.api_key}"
    
    def get_artwork_page(self, page: int, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch a page of artworks"""
        url = f"{self.museum_info.base_url}"  # base_url already includes /artworks
        params['page'] = page
        
        logging.debug(f"Requesting url: {url} with params: {params}")
        
        response = self.session.get(url, params=params, timeout=(5, 30))
        response.raise_for_status()
        return response.json()
    
    def _get_artwork_details_impl(self, artwork_id: str) -> ArtworkMetadata:
        '''Implement artwork details fetching for AIC'''
        url = f"{self.museum_info.base_url}/{artwork_id}"
        
        # Add debug logging
        logging.debug(f"Fetching artwork details from: {url}")
        
        try:
            response = self.session.get(url, timeout=(5, 30))
            response.raise_for_status()
            return ArtworkMetadata.from_aic_response(response.json()['data'])
        except Exception as e:
            logging.error(f"Error fetching details for artwork {artwork_id}: {e}")
            raise
    
    def get_departments(self) -> Dict[str, Any]:
        """Get department listings"""
        url = f"{self.museum_info.base_url}/departments"
        response = self.session.get(url, timeout=(5, 30))
        response.raise_for_status()
        return response.json()
    
    def build_image_url(self, image_id: str, **kwargs) -> str:
        """Build IIIF image URL"""
        size = kwargs.get('size', 'full')
        return f"https://www.artic.edu/iiif/2/{image_id}/{size}/0/default.jpg"
    
    def search_artworks(self, query: str, **kwargs) -> Dict[str, Any]:
        """Search for artworks"""
        url = f"{self.museum_info.base_url}/artworks/search"
        params = {'q': query, **kwargs}
        response = self.session.get(url, params=params, timeout=(5, 30))
        response.raise_for_status()
        return response.json()
        
    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        '''Implement paginated collection iteration for AIC with resumption'''
        try:
            # Determine starting page from progress tracker
            start_page = 1
            if isinstance(self.progress_tracker, AICProgressTracker):
                last_page = self.progress_tracker.get_last_page()
                if last_page > 0:
                    start_page = last_page
                    logging.info(f"Resuming from page {start_page}")
            
            current_page = start_page
            while True:
                try:
                    # Get page of artworks
                    response = self.get_artwork_page(current_page, params)
                    if not response or not response.get('data'):
                        break
                    
                    for item in response['data']:
                        try:
                            artwork = self._get_artwork_details_impl(str(item['id']))
                            if artwork:
                                # Add page info to artwork metadata
                                artwork.page = current_page
                                # Update progress tracker
                                if isinstance(self.progress_tracker, AICProgressTracker):
                                    self.progress_tracker.update_page(current_page)
                                yield artwork
                        except Exception as e:
                            logging.error(f"Error processing artwork {item['id']}: {e}")
                            continue
                    
                    current_page += 1
                    
                except Exception as e:
                    logging.error(f"Error processing page {current_page}: {e}")
                    raise
                    
        except Exception as e:
            logging.error(f"Error in collection iteration: {e}")
            raise
            
    def get_collection_info(self) -> Dict[str, Any]:
        """Get basic collection information"""
        url = f"{self.museum_info.base_url}/search"
        params = {'limit': 0}  # Just get total count, no results
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            return {
                'total_objects': data.get('pagination', {}).get('total', 0)
            }
        except Exception as e:
            logging.error(f"Error getting collection info: {e}")
            return {'total_objects': 0}
        
class AICImageProcessor(MuseumImageProcessor):
    """Art Institute of Chicago image processor implementation"""
    
    def process_image(self, image_data: bytes, metadata: ArtworkMetadata) -> Path:
        """Process and save an artwork image"""
        try:
            # Open image from bytes
            image = Image.open(BytesIO(image_data))
            
            # Generate filename and full path
            filename = self.generate_filename(metadata)
            filepath = self.output_dir / filename
            
            # Save the image
            image.save(filepath, format='JPEG', quality=95)
            return filepath
            
        except Exception as e:
            raise RuntimeError(f"Failed to process image for artwork {metadata.id}: {str(e)}")
    
    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        """Generate a filename for the artwork"""
        # Clean up artist and title for filename
        return sanitize_filename(
            id= f"AIC_{metadata.id}",
            title= metadata.title,
            artist= metadata.artist,
            max_length= 255
        )

@dataclass
class AICProgressState(ProgressState):
    last_page: int = 0
    total_pages: int = 0

class AICProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path):
        self.state = AICProgressState()
        super().__init__(progress_file)
    
    def get_state_dict(self) -> Dict[str, Any]:
        return {
            'processed_ids': list(self.state.processed_ids),
            'success_ids': list(self.state.success_ids),
            'failed_ids': list(self.state.failed_ids),
            'error_log': self.state.error_log,
            'last_page': self.state.last_page,
            'total_pages': self.state.total_pages
        }
    
    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get('processed_ids', []))
        self.state.success_ids = set(data.get('success_ids', []))
        self.state.failed_ids = set(data.get('failed_ids', []))
        self.state.error_log = data.get('error_log', {})
        self.state.last_page = data.get('last_page', 0)
        self.state.total_pages = data.get('total_pages', 0)
    
    def update_page(self, page: int) -> None: 
        '''Update last processed page numebr'''
        self.state.last_page = page
        self._save_progress()
    
    def get_last_page(self) -> int: 
        '''Get last processed page number'''
        return self.state.last_page