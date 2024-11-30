from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Iterator
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging
from pathlib import Path
import requests_cache

from .schemas import ArtworkMetadata
from ..settings.types import MuseumInfo
from ..settings import settings
from ..utils import sanitize_filename

class MuseumAPIClient(ABC): 
    '''Abstract base class for museum API clients'''
    
    def __init__(self, museum_info: MuseumInfo, api_key: Optional[str] = None, cache_file: Optional[Path] = None):
        self.museum_info = museum_info
        self.api_key = api_key
        self.session = self._create_session()
        
        if cache_file:
            import requests_cache
            requests_cache.install_cache(str(cache_file), backend= 'sqlite')
    
    def _create_session(self) -> requests.Session: 
        '''Create a configured requests session with retry logic'''
        session = requests.Session()
        
        headers = {}
        if self.museum_info.user_agent:
            headers['User-Agent'] = self.museum_info.user_agent
        
        if self.api_key: 
            headers['Authorization'] = self._get_auth_header()
            
        session.headers.update(headers)
        
        # Retry configuration
        retry_strategy = Retry(
            total=5, 
            backoff_factor=1, 
            status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        return session

    @abstractmethod
    def _get_auth_header(self) -> str: 
        '''Return authentication header value'''
        pass
    
    def iter_collection(self, **params) -> Iterator[ArtworkMetadata]:
        '''
        Main interface for iterating through a museum's collection.
        Each museum client implements its own _iter_collection_impl method.
        '''
        try:
            yield from self._iter_collection_impl(**params)
        except Exception as e:
            logging.error(f"Error iterating through collection: {e}")
            return
    
    @abstractmethod
    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        '''Implementation specific to each museum API'''
        pass
    
    @abstractmethod
    def get_collection_info(self) -> Dict[str, Any]:
        pass 
    
    def get_artwork_details(self, artwork_id: str) -> ArtworkMetadata:
        '''
        Get detailed info for a specific artwork.
        This could be overridden if needed but provides a common implementation.
        '''
        try:
            return self._get_artwork_details_impl(artwork_id)
        except Exception as e:
            logging.error(f"Error fetching artwork {artwork_id}: {e}")
            raise

    @abstractmethod
    def _get_artwork_details_impl(self, artwork_id: str) -> ArtworkMetadata:
        '''Implementation specific to each museum API'''
        pass

class MuseumImageProcessor(ABC): 
    '''ABC for processing museum images'''
    
    def __init__(self, output_dir: Path, museum_info: MuseumInfo): 
        self.output_dir = output_dir
        self.museum_info = museum_info
        self._ensure_output_dir()
    
    def _ensure_output_dir(self) -> None:
        '''Ensure output directory exists'''
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    @abstractmethod
    def process_image(self, image_data: bytes, metadata: ArtworkMetadata) -> Path: 
        '''Process and save image, return path to saved file'''
        pass
    
    @abstractmethod
    def generate_filename(self, metadata: ArtworkMetadata) -> str: 
        pass 

class ArtworkMetadataFactory(ABC):
    """Abstract base factory for creating ArtworkMetadata objects"""
    
    @abstractmethod
    def create_metadata(self, data: Dict[str, Any]) -> ArtworkMetadata:
        """Create ArtworkMetadata from API response data"""
        pass
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Helper method to parse date strings to datetime objects"""
        if not date_str:
            return None
        try:
            if date_str.isdigit():
                return datetime(year=int(date_str), month=1, day=1)
            return None  # Handle other date formats as needed
        except ValueError:
            return None
    
    def _parse_year(self, year_str: str) -> Optional[int]:
        """Helper method to parse year strings"""
        if year_str and year_str.isdigit():
            return int(year_str)
        return None