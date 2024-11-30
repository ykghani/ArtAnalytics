from dataclasses import dataclass
from typing import Optional, Dict, List, Any
from datetime import datetime

@dataclass
class ArtworkMetadata:
    """Standardized metadata for artwork across different museums"""
    
    # Core Identifiers
    id: str
    accession_number: str
    
    # Basic Artwork Info
    title: str
    artist: str
    artist_display: Optional[str] = None
    artist_bio: Optional[str] = None
    artist_nationality: Optional[str] = None
    artist_birth_year: Optional[int] = None
    artist_death_year: Optional[int] = None
    
    # Dates
    date_display: Optional[str] = None  # Human readable
    date_start: Optional[str] = None    # For analytics
    date_end: Optional[str] = None      # For analytics
    
    # Physical Details
    medium: Optional[str] = None
    dimensions: Optional[str] = None  # Human readable
    height_cm: Optional[float] = None  # For analytics
    width_cm: Optional[float] = None   # For analytics
    depth_cm: Optional[float] = None   # For analytics
    diameter_cm: Optional[float] = None # For analytics
    
    # Classification & Categories
    department: Optional[str] = None
    artwork_type: Optional[str] = None
    culture: Optional[List[str]] = None
    style: Optional[str] = None
    
    # Rights & Display
    is_public_domain: bool = False
    credit_line: Optional[str] = None
    is_on_view: Optional[bool] = None
    is_highlight: Optional[bool] = None
    is_boosted: Optional[bool] = None 
    boost_rank: Optional[int] = None
    has_not_been_viewed_much: Optional[bool] = None
    
    # Rich Content
    description: Optional[str] = None
    short_description: Optional[str] = None
    provenance: Optional[str] = None
    inscriptions: Optional[List[str]] = None
    fun_fact: Optional[str] = None  # CMA's did_you_know
    style_titles: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    
    # Images
    primary_image_url: Optional[str] = None
    image_urls: Optional[Dict[str, str]] = None
    
    # Analytics Support
    colorfulness: Optional[float] = None  # AIC's colorfulness score
    color_h: Optional[int] = None        # Hue from AIC
    color_s: Optional[int] = None        # Saturation from AIC
    color_l: Optional[int] = None        # Lightness from AIC

@dataclass
class Dimensions:
    """Class for handling artwork dimensions consistently across all museums"""
    height_cm: Optional[float] = None
    width_cm: Optional[float] = None
    depth_cm: Optional[float] = None
    diameter_cm: Optional[float] = None
    
    @classmethod
    def from_meters(cls, height: Optional[float] = None, 
                   width: Optional[float] = None,
                   depth: Optional[float] = None, 
                   diameter: Optional[float] = None) -> 'Dimensions':
        """Convert dimensions from meters to centimeters"""
        return cls(
            height_cm=height * 100 if height is not None else None,
            width_cm=width * 100 if width is not None else None,
            depth_cm=depth * 100 if depth is not None else None,
            diameter_cm=diameter * 100 if diameter is not None else None
        )
    
    @classmethod
    def from_cm(cls, height: Optional[float] = None,
                width: Optional[float] = None,
                depth: Optional[float] = None,
                diameter: Optional[float] = None) -> 'Dimensions':
        """Create dimensions directly from centimeter measurements"""
        return cls(
            height_cm=height,
            width_cm=width,
            depth_cm=depth,
            diameter_cm=diameter
        )