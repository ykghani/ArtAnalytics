from dataclasses import dataclass
from typing import Optional
from datetime import datetime

@dataclass
class MuseumInfo:
    """Basic information about a museum API"""
    name: str
    base_url: str
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
    date_created: Optional[str] = None
    medium: Optional[str] = None
    dimensions: Optional[str] = None
    credit_line: Optional[str] = None
    image_id: Optional[str] = None
    department: Optional[str] = None
    is_public_domain: bool = False
    
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