from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from datetime import datetime

# from .artwork_metadata import ArtworkMetadata, Dimensions
from src.museums.base import ArtworkMetadataFactory

class MetArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Met Museum artwork metadata"""
    
    def create_metadata(self, data: Dict[str, Any]) -> 'ArtworkMetadata':
        # Extract dimensions
        dimensions = None
        if measurements := data.get('measurements', []):
            for item in measurements:
                if item.get('elementName') == 'Overall':
                    elem_measurements = item.get('elementMeasurements', {})
                    dimensions = Dimensions.from_cm(
                        height=elem_measurements.get('Height'),
                        width=elem_measurements.get('Width'),
                        depth=elem_measurements.get('Depth'),
                        diameter=elem_measurements.get('Diameter')
                    )
        
        # Build artist display
        artist_display = None
        if artist_name := data.get('artistDisplayName'):
            if artist_bio := data.get('artistDisplayBio'):
                artist_display = f"{artist_name}\n{artist_bio}"
            else:
                artist_display = artist_name
        
        return ArtworkMetadata(
            id=str(data['objectID']),
            accession_number=data.get('accessionNumber', ''),
            title=data.get('title', 'Untitled'),
            artist=data.get('artistDisplayName', 'Unknown'),
            artist_display=artist_display,
            artist_nationality=data.get('artistNationality'),
            artist_birth_year=self._parse_year(data.get('artistBeginDate')),
            artist_death_year=self._parse_year(data.get('artistEndDate')),
            date_display=data.get('objectDate'),
            date_start=self._parse_date(str(data.get('objectBeginDate'))),
            date_end=self._parse_date(str(data.get('objectEndDate'))),
            medium=data.get('medium'),
            dimensions=dimensions,
            department=data.get('department'),
            artwork_type=data.get('objectName'),
            culture=[data.get('culture')] if data.get('culture') else None,
            is_public_domain=data.get('isPublicDomain', False),
            credit_line=data.get('creditLine'),
            is_on_view=bool(data.get('GalleryNumber')),
            is_highlight=data.get('isHighlight', False),
            description=None,
            inscriptions=data.get('inscriptions', []),
            primary_image_url=data.get('primaryImage')
        )

class AICArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Art Institute of Chicago artwork metadata"""
    
    def create_metadata(self, data: Dict[str, Any]) -> 'ArtworkMetadata':
        # Extract dimensions
        dimensions = None
        if dims := data.get('dimensions_detail', []):
            dimensions = Dimensions.from_cm(
                height=next((d.get('height_cm') for d in dims), None),
                width=next((d.get('width_cm') for d in dims), None),
                depth=next((d.get('depth_cm') for d in dims), None),
                diameter=next((d.get('diameter_cm') for d in dims), None)
            )
        
        color = data.get('color', {})
        
        return ArtworkMetadata(
            id=str(data['id']),
            accession_number=data.get('main_reference_number', ''),
            title=data.get('title', 'Untitled'),
            artist=data.get('artist_title', 'Unknown Artist'),
            artist_display=data.get('artist_display', 'Unknown Artist'),
            date_display=data.get('date_display'),
            date_start=self._parse_date(str(data.get('date_start'))),
            date_end=self._parse_date(str(data.get('date_end'))),
            medium=data.get('medium_display'),
            dimensions=dimensions,
            department=data.get('department_title'),
            artwork_type=data.get('artwork_type_title'),
            culture=[data.get('place_of_origin')] if data.get('place_of_origin') else None,
            is_public_domain=data.get('is_public_domain', False),
            credit_line=data.get('credit_line'),
            is_on_view=data.get('is_on_view', False),
            is_highlight=data.get('is_highlight', False),
            is_boosted=data.get('is_boosted', False),
            boost_rank=data.get('boost_rank'),
            has_not_been_viewed_much=data.get('has_not_been_viewed_much', True),
            description=data.get('description'),
            short_description=data.get('short_description'),
            provenance=data.get('provenance_text'),
            inscriptions=[data.get('inscriptions')] if data.get('inscriptions') else [],
            style_titles=data.get('style_titles', []),
            keywords=data.get('subject_titles', []),
            colorfulness=data.get('colorfulness'),
            color_h=color.get('h'),
            color_s=color.get('s'),
            color_l=color.get('l')
        )

class CMAArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Cleveland Museum of Art artwork metadata"""
    
    def create_metadata(self, data: Dict[str, Any]) -> ArtworkMetadata:
        # Extract dimensions safely
        dimensions = None
        if dims := data.get('dimensions', {}).get('framed', {}):
            dimensions = Dimensions.from_meters(
                height=dims.get('height'),
                width=dims.get('width'),
                depth=dims.get('depth'),
                diameter=dims.get('diameter')
            )
        
        # Extract inscriptions safely
        inscriptions = []
        if inscr_list := data.get('inscriptions', []):
            inscriptions = [insc['inscription'] for insc in inscr_list if 'inscription' in insc]
        
        # Handle creator info safely
        creators = data.get('creators', [])
        creator = creators[0] if creators else {}  # Use empty dict if no creators
        
        return ArtworkMetadata(
            id=str(data['id']),
            accession_number=data.get('accession_number', ''),
            title=data.get('title', 'Untitled'),
            artist=creator.get('description', 'Unknown'),
            artist_display=creator.get('description'),
            artist_bio=creator.get('biography'),
            artist_birth_year=self._parse_year(creator.get('birth_year')),
            artist_death_year=self._parse_year(creator.get('death_year')),
            date_display=data.get('creation_date'),
            date_start=self._parse_date(str(data.get('creation_date_earliest'))),
            date_end=self._parse_date(str(data.get('creation_date_latest'))),
            medium=data.get('technique'),
            dimensions=dimensions,
            department=data.get('department'),
            artwork_type=data.get('type'),
            culture=data.get('culture'),
            is_public_domain=data.get('share_license_status') == 'CC0',
            credit_line=data.get('creditline'),
            is_on_view=bool(data.get('current_location')),
            is_highlight=data.get('is_highlight', False),
            description=data.get('description'),
            short_description=data.get('tombstone'),
            provenance=' '.join(p.get('description', '') for p in data.get('provenance', [])),
            inscriptions=inscriptions,
            fun_fact=data.get('did_you_know'),
            primary_image_url=data.get('images', {}).get('web', {}).get('url')
            )