from typing import Dict, Any, Optional, Iterator, Set
from pathlib import Path
from PIL import Image
import logging
from io import BytesIO
import json
from dataclasses import dataclass, field 

from .base import MuseumAPIClient, MuseumImageProcessor
from ..config import settings
# from ..log_level import log_level
from .schemas import ArtworkMetadata, MuseumInfo, AICArtworkFactory
from ..download.progress_tracker import BaseProgressTracker, ProgressState
from ..utils import sanitize_filename, setup_logging

class AICClient(MuseumAPIClient):
    """Art Institute of Chicago API Client implementation"""
    
    def __init__(self, museum_info: MuseumInfo, api_key: Optional[str] = None, 
                 cache_file: Optional[Path] = None, progress_tracker: Optional[BaseProgressTracker] = None,
                 data_dump_path: Optional[Path] = None):
        super().__init__(museum_info=museum_info, api_key=api_key, cache_file=cache_file)
        self.progress_tracker = progress_tracker
        self.artwork_factory = AICArtworkFactory()
        self.data_dump_path = data_dump_path
        self.logger = setup_logging(settings.logs_dir, settings.log_level, 'aic')
    
    def _get_auth_header(self) -> str:
        if not self.api_key:
            return ""
        return f"Bearer {self.api_key}"
    
    def get_artwork_page(self, page: int, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch a page of artworks"""
        url = f"{self.museum_info.base_url}"  # base_url already includes /artworks
        params['page'] = page
        
        self.logger.debug(f"Requesting url: {url} with params: {params}")
        
        response = self.session.get(url, params=params, timeout=(5, 30))
        response.raise_for_status()
        return response.json()
    
    def _get_artwork_details_impl(self, artwork_id: str) -> ArtworkMetadata:
        '''Implement artwork details fetching for AIC'''
        url = f"{self.museum_info.base_url}/{artwork_id}"
        
        self.logger.debug(f"Fetching artwork details from: {url}")
        
        try:
            response = self.session.get(url, timeout=(5, 30))
            response.raise_for_status()
            # return ArtworkMetadata.from_aic_response(response.json()['data'])
            return self.artwork_factory.create_metadata(response.json()['data'])
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
        params = {'q': query, **kwargs}
        response = self.session.get(url, params=params, timeout=(5, 30))
        response.raise_for_status()
        return response.json()
    
    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        """Choose data source based on availability"""
        self.logger.debug(f"Data dump path type: {type(self.data_dump_path)}")
        self.logger.debug(f"Data dump path: {self.data_dump_path}")
        self.logger.debug(f"Data dump exists: {self.data_dump_path and self.data_dump_path.exists() if self.data_dump_path else False}")
        
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
                params['limit'] = limit
                data = self.get_artwork_page(page, params)
                
                if not data.get('data'):
                    break
                    
                for artwork in data['data']:
                    yield self.artwork_factory.create_metadata(artwork)
                    
                if isinstance(self.progress_tracker, AICProgressTracker):
                    self.progress_tracker.state.last_page = page
                    total_pages = data.get('pagination', {}).get('total_pages', 0)
                    self.progress_tracker.state.total_pages = total_pages
                    
                page += 1
                
            except Exception as e:
                self.logger.error(f"Error fetching page {page}: {e}")
                raise
        
    def _iter_data_dump(self) -> Iterator[ArtworkMetadata]:
        """Iterate through JSON files in data dump directory"""
        try:
            # Get and sort all JSON files - this creates a stable order
            artwork_files = sorted(list(self.data_dump_path.glob('*.json')))
            total_files = len(artwork_files)
            
            if isinstance(self.progress_tracker, AICProgressTracker):
                self.progress_tracker.state.total_files = total_files
                start_idx = self.progress_tracker.state.last_processed_index
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
                        if isinstance(self.progress_tracker, AICProgressTracker):
                            self.progress_tracker.state.last_processed_index = idx
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
    def __init__(self, output_dir: Path, museum_info: MuseumInfo):
        super().__init__(output_dir, museum_info)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, 'aic')
    
    def process_image(self, image_data: bytes, metadata: ArtworkMetadata) -> Path:
        """Process and save an artwork image"""
        try:
            # Open image from bytes
            self.logger.debug(f"Processing image for artwork {metadata.id}")
            image = Image.open(BytesIO(image_data))
            
            # Generate filename and full path
            filename = self.generate_filename(metadata)
            filepath = self.output_dir / filename
            
            # Save the image
            image.save(filepath, format='JPEG', quality=95)
            self.logger.artwork(f"Saved image to {filepath}")
            return filepath
            
        except Exception as e:
            self.logger.error(f"Failed to process image for artwork {metadata.id}: {str(e)}")
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
    processed_ids: Set[str] = field(default_factory=set)  
    success_ids: Set[str] = field(default_factory=set)    
    failed_ids: Set[str] = field(default_factory=set)     
    error_log: Dict[str, Dict[str, str]] = field(default_factory=dict)
    last_processed_index: int = 0  # For data dump processing
    total_files: int = 0

class AICProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path):
        self.progress_file = progress_file  
        self.state = AICProgressState()     
        self.logger = setup_logging(settings.logs_dir, settings.log_level, 'aic')  
        self._load_progress()
    
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