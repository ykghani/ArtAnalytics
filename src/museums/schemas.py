from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, List, Any

@dataclass
class MuseumInfo:
    """Basic information about a museum API"""
    name: str
    base_url: str
    code: str 
    user_agent: Optional[str] = None
    api_version: str = "v1"
    rate_limit: float = 1.0  # requests per second
    requires_api_key: bool = False

@dataclass
class ArtworkMetadata:
    """Standardized metadata for artwork across different museums"""
    id: str
    title: str
    artist: str
    artist_display: Optional[str] = None 
    date_created: Optional[str] = None
    medium: Optional[str] = None
    dimensions: Optional[str] = None
    credit_line: Optional[str] = None
    image_id: Optional[str] = None
    department: Optional[str] = None
    is_public_domain: bool = False
    primary_image_url: Optional[str] = None
    detailed_dimensions: Optional[List[Any]] = None
    description: Optional[str] = None
    short_description: Optional[str] = None
    colorfulness: Optional[float] = None
    color: Optional[Dict[str, Any]] = None
    is_on_view: Optional[bool] = None
    tags: Optional[List[str]] = None
    is_highlight: Optional[bool] = False
    page: Optional[int] = None
    total_objects: Optional[int] = None


class ArtworkMetadataFactory(ABC):
    """Abstract base factory for creating ArtworkMetadata objects"""
    
    @abstractmethod
    def create_metadata(self, data: Dict[str, Any]) -> ArtworkMetadata:
        """Create ArtworkMetadata from API response data"""
        pass

class AICArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Art Institute of Chicago artwork metadata"""
    
    def create_metadata(self, data: Dict[str, Any]) -> ArtworkMetadata:
        return ArtworkMetadata(
            id=str(data['id']),
            title=data.get('title', 'Untitled'),
            artist=data.get('artist_title', 'Unknown Artist'), 
            artist_display=data.get('artist_display'),
            date_created=data.get('date_display'),
            medium=data.get('medium_display'),
            dimensions=data.get('dimensions'),
            credit_line=data.get('credit_line'),
            image_id=data.get('image_id'),
            department=data.get('department_title'),
            is_public_domain=data.get('is_public_domain', False),
            is_on_view=data.get('is_on_view', False),
            colorfulness=data.get('colorfulness'),
            color=data.get('color')
        )

class MetArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Met Museum artwork metadata"""
    
    def create_metadata(self, data: Dict[str, Any]) -> ArtworkMetadata:
        return ArtworkMetadata(
            id=str(data['objectID']),
            title=data.get('title', 'Untitled'), 
            artist=data.get('artistDisplayName', 'Unknown'),
            artist_display=data.get('artistDisplayBio'),
            date_created=data.get('objectDate'),
            medium=data.get('medium'),
            dimensions=data.get('dimensions'),
            credit_line=data.get('creditLine'),
            department=data.get('department'),
            is_public_domain=data.get('isPublicDomain', False),
            primary_image_url=data.get('primaryImage'),
            detailed_dimensions=data.get('measurements'),
            tags=data.get('tags'),
            is_highlight=data.get('isHighlight', False)
        ) 

class CMAArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Cleveland Museum of Art artwork metadata"""
    
    def create_metadata(self, data: Dict[str, Any]) -> ArtworkMetadata:
        # Handle images - get the best available URL
        images = data.get('images', {})
        image_url = None
        for image_type in ['web', 'print', 'full']:
            if image_type in images and 'url' in images[image_type]:
                image_url = images[image_type]['url']
                break
                
        # Extract creator info
        creators = data.get('creators', [])
        creator = creators[0] if creators else {}
        
        # Extract dimensions if available
        dimensions = data.get('measurements')
        detailed_dims = data.get('dimensions', {}).get('framed', {})  # Get framed dimensions
        
        return ArtworkMetadata(
            id=str(data['id']),
            title=data.get('title', 'Untitled'),
            artist=creator.get('description', 'Unknown'),
            artist_display=creator.get('description'),  # CMA uses same field for both
            date_created=data.get('creation_date'),
            medium=data.get('technique'),
            dimensions=dimensions,
            detailed_dimensions=detailed_dims,
            credit_line=data.get('creditline'),
            department=data.get('department'),
            is_public_domain=data.get('share_license_status') == 'CC0',
            primary_image_url=image_url,
            description=data.get('description'),
            short_description=data.get('tombstone'),
            is_on_view=bool(data.get('current_location')),
            is_highlight=data.get('is_highlight', False),
            tags=data.get('tags', [])
        )