from dataclasses import dataclass
from typing import Optional, Dict, List, Any
from datetime import datetime

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
    
    @classmethod
    def from_aic_response(cls, data: dict) -> 'ArtworkMetadata':
        """Create metadata instance from AIC API response"""
        return cls(
            id=str(data['id']),
            title=data.get('title', 'Untitled'),
            artist=data.get('artist_display', 'Unknown Artist'),
            date_created=data.get('date_display'),
            medium=data.get('medium_display'),
            dimensions=data.get('dimensions'),
            credit_line=data.get('credit_line'),
            image_id=data.get('image_id'),
            department=data.get('department_title'),
            is_public_domain=data.get('is_public_domain', False)
        )
    
    @classmethod
    def from_met_response(cls, data: dict) -> 'ArtworkMetadata':
        '''Create metadata from Met API response'''
        return cls(
            id = str(data['objectID']),
            title = data.get('title', 'Untitled'),
            artist = data.get('artistDisplayName', 'Unknown'),
            artist_display = data.get('artistDisplayBio', ""),
            date_created = data.get('objectDate'),
            medium = data.get('medium'),
            dimensions = data.get('dimensions'),
            credit_line = data.get('creditLine'),
            department = data.get('department'),
            is_public_domain = data.get('isPublicDomain', False),
            primary_image_url = data.get('primaryImage'),
            detailed_dimensions = data.get('measurements'),
            tags = data.get('tags'),
            is_highlight = data.get('isHighlight', False)
        )
        