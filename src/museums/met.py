from typing import Dict, List, Any, Optional, Iterator
from pathlib import Path
from PIL import Image
from io import BytesIO
import logging

from requests.sessions import Session as Session

from .base import MuseumAPIClient, MuseumImageProcessor
from .schemas import ArtworkMetadata, MuseumInfo
from ..utils import sanitize_filename

class MetClient(MuseumAPIClient):
    '''Metropolitan Museum of Art Client Implmentation'''
    
    def __init__(self, museum_info: MuseumInfo, api_key: Optional[str] = None, cache_file: Optional[Path] = None):
        super().__init__(museum_info= museum_info, api_key= api_key, cache_file= cache_file)
    
    def _get_auth_header(self) -> str:
        '''Met does not require authentication'''
        return ""
    
    def get_image(self, image_url: str) -> bytes: 
        '''Download image directly from Met's url'''
        if not image_url: 
            raise ValueError("No image URL provided")
    
    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        '''Implement ID-based collection iteration for Met'''
        # First get all object IDs
        url = f"{self.museum_info.base_url}/objects"
    
        try:
            # Get all object IDs first
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
            
            total_ids = data.get('total', 0)
            object_ids = data.get('objectIDs', [])
            
            if not object_ids:
                logging.warning(f"No objects found matching criteria. Total: {total_ids}")
                return
                
            logging.info(f"Found {total_ids} objects to process")
            
            # Iterate through IDs, remove slicing when it works 
            for object_id in object_ids[:10]:
                try:
                    artwork = self._get_artwork_details_impl(str(object_id))
                    if artwork:  # Only yield if we got valid artwork data
                        yield artwork
                except Exception as e:
                    logging.error(f"Error getting metadata from artwork {object_id}: {e}")
                    continue

        except Exception as e:
            logging.error(f"Error fetching object IDs: {e}")
            raise
                
    def _get_artwork_details_impl(self, object_id: str) -> Optional[ArtworkMetadata]:
        '''Implement artwork details fetching for Met'''
        url = f"{self.museum_info.base_url}/objects/{object_id}"
        
        try:
            response = self.session.get(url, timeout=(5, 30))
            response.raise_for_status()
            return ArtworkMetadata.from_met_response(response.json())
        
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