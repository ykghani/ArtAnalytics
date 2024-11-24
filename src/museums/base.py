from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Iterator
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging
from pathlib import Path
import requests_cache

from .schemas import ArtworkMetadata, MuseumInfo
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
    
    @abstractmethod
    def get_artwork_page(self, page: int, params: Dict[str, Any]) -> Dict[str, Any]:
        '''Get a page of artwork listings'''
        pass
    
    @abstractmethod
    def get_artwork_details(self, artwork_id: str) -> ArtworkMetadata:
        '''Get detailed info for a specific artwork'''
        pass
    
    @abstractmethod
    def get_departments(self) -> Dict[str, Any]:
        '''Get mapping of department IDs to names'''
        pass
    
    @abstractmethod
    def build_image_url(self, image_id: str, **kwargs) -> str:
        '''Build url for downloading artworks'''
        pass
        
    @abstractmethod
    def search_artworks(self, query: str, **kwargs) -> Dict[str, Any]:
        '''Search for artworks with museum specific params'''
        pass
    
    def iter_collection(self, **params) -> Iterator[ArtworkMetadata]:
        '''Iterate over entire collection with specified params'''
        page = 1
        while True: 
            try:
                response = self.get_artwork_page(page, params)
                if not response or not response.get('data'):
                    break 
                
                for item in response['data']:
                    try:
                        yield self.get_artwork_details(str(item['id']))
                    except Exception as e:
                        logging.error(f"Error processing artwork {item['id']}: {e}")
                        continue
                    
                page += 1
            except Exception as e:
                logging.error(f"Error fetching page {page}: {e}")
                break

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