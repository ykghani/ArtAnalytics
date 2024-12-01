from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any

from ..config import log_level, settings
from .museum_info import MuseumInfo
from ..utils import setup_logging

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
    date_display: Optional[str] = None
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    
    # Physical Details
    medium: Optional[str] = None
    dimensions: Optional[str] = None
    height_cm: Optional[float] = None
    width_cm: Optional[float] = None
    depth_cm: Optional[float] = None
    diameter_cm: Optional[float] = None
    
    # Classification & Categories
    department: Optional[str] = None
    artwork_type: Optional[str] = None
    culture: Optional[List[str]] = field(default_factory=list)
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
    inscriptions: Optional[List[str]] = field(default_factory=list)
    fun_fact: Optional[str] = None
    style_titles: Optional[List[str]] = field(default_factory=list)
    keywords: Optional[List[str]] = field(default_factory=list)
    
    # Images
    primary_image_url: Optional[str] = None
    image_urls: Optional[Dict[str, str]] = field(default_factory=dict)
    
    # Analytics Support
    colorfulness: Optional[float] = None
    color_h: Optional[int] = None
    color_s: Optional[int] = None
    color_l: Optional[int] = None

class ArtworkMetadataFactory(ABC):
    """Abstract base factory for creating ArtworkMetadata objects"""
    def __init__(self, museum_code: str):
        self.logger = setup_logging(settings.logs_dir, log_level, museum_code)
        
    @abstractmethod
    def create_metadata(self, data: Dict[str, Any]) -> ArtworkMetadata:
        pass

class AICArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Art Institute of Chicago artwork metadata"""
    def __init__(self):
        super().__init__('aic')
    
    def create_metadata(self, data: Dict[str, Any]) -> ArtworkMetadata:
        # Extract dimensions data
        dimensions_detail = data.get('dimensions_detail', [{}])[0]
        height = dimensions_detail.get('height_cm')
        width = dimensions_detail.get('width_cm')
        depth = dimensions_detail.get('depth_cm')
        diameter = dimensions_detail.get('diameter_cm')

        # Extract color data
        color_data = data.get('color', {})
        
        # Extract artist data from first constituent if available
        artist_info = data.get('artist_display', '').split('\n')[0] if data.get('artist_display') else 'Unknown Artist'
        
        return ArtworkMetadata(
            id=str(data['id']),
            accession_number=data.get('main_reference_number', ''),
            title=data.get('title', 'Untitled'),
            artist=data.get('artist_title', artist_info),
            artist_display=data.get('artist_display'),
            artist_bio=None,  # AIC doesn't provide this
            artist_nationality=None,  # AIC doesn't provide this directly
            artist_birth_year=None,  # Would need to parse from artist_display
            artist_death_year=None,  # Would need to parse from artist_display
            
            date_display=data.get('date_display'),
            date_start=str(data.get('date_start')) if data.get('date_start') else None,
            date_end=str(data.get('date_end')) if data.get('date_end') else None,
            
            medium=data.get('medium_display'),
            dimensions=data.get('dimensions'),
            height_cm=height,
            width_cm=width,
            depth_cm=depth,
            diameter_cm=diameter,
            
            department=data.get('department_title'),
            artwork_type=data.get('artwork_type_title'),
            culture=[data.get('place_of_origin')] if data.get('place_of_origin') else [],
            style=None,  # AIC doesn't provide this directly
            
            is_public_domain=data.get('is_public_domain', False),
            credit_line=data.get('credit_line'),
            is_on_view=data.get('is_on_view', False),
            is_highlight=False,  # AIC doesn't have this concept
            is_boosted=data.get('is_boosted', False),
            boost_rank=data.get('boost_rank'),
            has_not_been_viewed_much=data.get('has_not_been_viewed_much', False),
            
            description=data.get('description'),
            short_description=data.get('short_description'),
            provenance=data.get('provenance_text'),
            inscriptions=[data.get('inscriptions')] if data.get('inscriptions') else [],
            fun_fact=None,  # AIC doesn't have this
            style_titles=data.get('style_titles', []),
            keywords=data.get('term_titles', []),
            
            primary_image_url=None,  # AIC uses IIIF images
            image_urls={},  # Would need to construct IIIF URLs
            
            colorfulness=data.get('colorfulness'),
            color_h=color_data.get('h'),
            color_s=color_data.get('s'),
            color_l=color_data.get('l')
        )

class MetArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Metropolitan Museum artwork metadata"""
    def __init__(self):
        super().__init__('met')
    
    def create_metadata(self, data: Dict[str, Any]) -> ArtworkMetadata:
        
        if not data:
            self.logger.debug(f"Received empty data")
            return None 
        
        try:
            # Extract measurements
            measurements = data.get('measurements', [])
            height = width = depth = diameter = None
            for measure in measurements:
                if 'Height' in measure.get('elementMeasurements', {}):
                    height = measure['elementMeasurements'].get('Height')
                if 'Width' in measure.get('elementMeasurements', {}):
                    width = measure['elementMeasurements'].get('Width')
                if 'Depth' in measure.get('elementMeasurements', {}):
                    depth = measure['elementMeasurements'].get('Depth')
                if 'Diameter' in measure.get('elementMeasurements', {}):
                    diameter = measure['elementMeasurements'].get('Diameter')

            artwork = ArtworkMetadata(
                id=str(data['objectID']),
                accession_number=data.get('accessionNumber', ''),
                title=data.get('title', 'Untitled'),
                artist=data.get('artistDisplayName', 'Unknown'),
                artist_display=data.get('artistDisplayBio'),
                artist_bio=None,  # Met provides this in artistDisplayBio
                artist_nationality=data.get('artistNationality'),
                artist_birth_year=int(data['artistBeginDate']) if data.get('artistBeginDate', '').isdigit() else None,
                artist_death_year=int(data['artistEndDate']) if data.get('artistEndDate', '').isdigit() else None,
                
                date_display=data.get('objectDate'),
                date_start=str(data.get('objectBeginDate')) if data.get('objectBeginDate') else None,
                date_end=str(data.get('objectEndDate')) if data.get('objectEndDate') else None,
                
                medium=data.get('medium'),
                dimensions=data.get('dimensions'),
                height_cm=height,
                width_cm=width,
                depth_cm=depth,
                diameter_cm=diameter,
                
                department=data.get('department'),
                artwork_type=data.get('objectName'),
                culture=[data.get('culture')] if data.get('culture') else [],
                style=None,  # Met doesn't provide this directly
                
                is_public_domain=data.get('isPublicDomain', False),
                credit_line=data.get('creditLine'),
                is_on_view=bool(data.get('GalleryNumber')),
                is_highlight=data.get('isHighlight', False),
                is_boosted=None,  # Met doesn't have this concept
                boost_rank=None,  # Met doesn't have this concept
                has_not_been_viewed_much=None,  # Met doesn't have this concept
                
                description=None,  # Met doesn't provide this
                short_description=None,  # Met doesn't provide this
                provenance=None,  # Met provides this but not in API
                inscriptions=[data.get('inscriptions')] if data.get('inscriptions') else [],
                fun_fact=None,  # Met doesn't have this
                style_titles=[],  # Met doesn't provide this
                keywords=[tag.get('term') for tag in data.get('tags', [])] if data.get('tags') else [],
                
                primary_image_url=data.get('primaryImage'),
                image_urls={'primary': data.get('primaryImage'), 'small': data.get('primaryImageSmall')} if data.get('primaryImage') else {},
                
                colorfulness=None,  # Met doesn't provide this
                color_h=None,  # Met doesn't provide this
                color_s=None,  # Met doesn't provide this
                color_l=None   # Met doesn't provide this
            )
            
            self.logger.artwork(f"Created metadata for artwork {data.get('objectID')}")
            return artwork
            
        except Exception as e:
            self.logger.error(f"Error creating metadata: {e}")
            return None

class CMAArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Cleveland Museum of Art artwork metadata"""
    def __init__(self):
        super().__init__('cma')
    
    def create_metadata(self, data: Dict[str, Any]) -> ArtworkMetadata:
        # Handle dimensions
        dimensions_data = data.get('dimensions', {}).get('framed', {})
        height = dimensions_data.get('height')
        width = dimensions_data.get('width')
        depth = dimensions_data.get('depth')
        
        # Extract creator info
        creators = data.get('creators', [])
        creator = creators[0] if creators else {}
        
        # Handle images
        images = data.get('images', {})
        image_urls = {}
        for img_type in ['web', 'print', 'full']:
            if img_type in images and 'url' in images[img_type]:
                image_urls[img_type] = images[img_type]['url']
        
        return ArtworkMetadata(
            id=str(data['id']),
            accession_number=data.get('accession_number', ''),
            title=data.get('title', 'Untitled'),
            artist=creator.get('description', 'Unknown'),
            artist_display=creator.get('description'),
            artist_bio=creator.get('biography'),
            artist_nationality=None,  # CMA provides this in biography
            artist_birth_year=int(creator['birth_year']) if creator.get('birth_year', '').isdigit() else None,
            artist_death_year=int(creator['death_year']) if creator.get('death_year', '').isdigit() else None,
            
            date_display=data.get('creation_date'),
            date_start=str(data.get('creation_date_earliest')) if data.get('creation_date_earliest') else None,
            date_end=str(data.get('creation_date_latest')) if data.get('creation_date_latest') else None,
            
            medium=data.get('technique'),
            dimensions=data.get('measurements'),
            height_cm=height,
            width_cm=width,
            depth_cm=depth,
            diameter_cm=None,  # CMA doesn't typically provide this
            
            department=data.get('department'),
            artwork_type=data.get('type'),
            culture=data.get('culture', []),
            style=None,  # CMA doesn't provide this directly
            
            is_public_domain=data.get('share_license_status') == 'CC0',
            credit_line=data.get('creditline'),
            is_on_view=bool(data.get('current_location')),
            is_highlight=data.get('is_highlight', False),
            is_boosted=None,  # CMA doesn't have this concept
            boost_rank=None,  # CMA doesn't have this concept
            has_not_been_viewed_much=None,  # CMA doesn't have this concept
            
            description=data.get('description'),
            short_description=data.get('tombstone'),
            provenance='\n'.join(p.get('description', '') for p in data.get('provenance', [])),
            inscriptions=[i.get('inscription') for i in data.get('inscriptions', [])],
            fun_fact=data.get('did_you_know'),
            style_titles=[],  # CMA doesn't provide this
            keywords=[tag.get('term') for tag in data.get('tags', [])] if data.get('tags') else [],
            
            primary_image_url=image_urls.get('web'),
            image_urls=image_urls,
            
            colorfulness=None,  # CMA doesn't provide this
            color_h=None,  # CMA doesn't provide this
            color_s=None,  # CMA doesn't provide this
            color_l=None   # CMA doesn't provide this
        )