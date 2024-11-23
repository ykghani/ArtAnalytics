from typing import Dict, Any, Optional
from pathlib import Path
from PIL import Image
from io import BytesIO

from .base import MuseumAPIClient, MuseumImageProcessor
from .schemas import ArtworkMetadata, MuseumInfo

class AICClient(MuseumAPIClient):
    """Art Institute of Chicago API Client implementation"""
    
    def __init__(self, museum_info: MuseumInfo, api_key: Optional[str] = None, cache_file: Optional[Path] = None):
        super().__init__(museum_info= museum_info, api_key= api_key, cache_file= cache_file)
    
    def _get_auth_header(self) -> str:
        if not self.api_key:
            return ""
        return f"Bearer {self.api_key}"
    
    def get_artwork_page(self, page: int, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch a page of artworks"""
        url = f"{self.museum_info.base_url}/artworks"
        params['page'] = page
        response = self.session.get(url, params=params, timeout=(5, 30))
        response.raise_for_status()
        return response.json()
    
    def get_artwork_details(self, artwork_id: str) -> ArtworkMetadata:
        """Get detailed metadata for a specific artwork"""
        url = f"{self.museum_info.base_url}/artworks/{artwork_id}"
        response = self.session.get(url, timeout=(5, 30))
        response.raise_for_status()
        data = response.json()['data']
        return ArtworkMetadata.from_aic_response(data)
    
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
        safe_title = "".join(c for c in metadata.title if c.isalnum() or c in (' ', '-', '_'))
        safe_artist = "".join(c for c in metadata.artist if c.isalnum() or c in (' ', '-', '_'))
        
        # Truncate if necessary
        if len(safe_title) > 50:
            safe_title = safe_title[:47] + "..."
        if len(safe_artist) > 30:
            safe_artist = safe_artist[:27] + "..."
            
        return f"AIC_{metadata.id}_{safe_title}_{safe_artist}.jpg"