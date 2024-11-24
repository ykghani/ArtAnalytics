from typing import Dict, List, Any, Optional
from pathlib import Path
from PIL import Image
from io import BytesIO

from .base import MuseumAPIClient, MuseumImageProcessor
from .schemas import ArtworkMetadata, MuseumInfo
from ..utils import sanitize_filename

class MetClient(MuseumAPIClient):
    '''Metropolitan Museum of Art Client Implmentation'''
    
    def __init__(self, museum_info: MuseumInfo, api_key: Optional[str] = None, cache_file: Optional[Path] = None):
        super().__init__(museum_info= museum_info, api_key= api_key, cache_file= cache_file)
        
    def _get_auth_header(self) -> str:
        if not self.api_key:
            return ""
        return f"Bearer {self.api_key}"
    
    def get_artwork_page(self, page: int, params: Dict[str, Any]) -> Dict[str, Any]:
        pass
    
    def get_artwork_details(self, object_id: str) -> ArtworkMetadata:
        '''Get detailed metadata for a specific object id '''
        url = f"{self.museum_info.base_url/{object_id}}"
        response = self.session.get(url, timeout= (5,30))
        response.raise_for_status()
        data = response.json()
        return ArtworkMetadata.from_met_response(data)
    
    def get_departments(self) -> Dict[str, Any]:
        pass
    
    def build_image_url(self, image_id: str, **kwargs) -> str:
        pass
    
    def search_artworks(self, query: str, **kwargs) -> Dict[str, Any]:
        pass
    
    def get_image(self, image_url: str) -> bytes: 
        '''Download image directly from Met's url'''
        if not image_url: 
            raise ValueError("No image URL provided")
    
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