from typing import Optional
from sqlalchemy.orm import Session
from .models import Artwork, Museum
from ..museums.schemas import ArtworkMetadata

class ArtworkRepository:
    def __init__(self, session: Session):
        self.session = session
    
    def create_or_update_artwork(self, metadata: ArtworkMetadata, museum_code: str, image_path: Optional[str] = None) -> Artwork:
        """Create or update artwork record with expanded metadata"""
        museum = self.session.query(Museum).filter_by(code=museum_code).first()
        if not museum:
            raise ValueError(f"Museum with code {museum_code} not found")
            
        artwork = self.session.query(Artwork).filter_by(
            museum_id=museum.id,
            original_id=metadata.id
        ).first()
        
        if not artwork:
            artwork = Artwork(
                museum_id=museum.id,
                original_id=metadata.id
            )
            self.session.add(artwork)
        
        # Update all fields from metadata
        artwork.accession_number = metadata.accession_number
        artwork.title = metadata.title
        artwork.artist = metadata.artist
        artwork.artist_display = metadata.artist_display
        artwork.artist_bio = metadata.artist_bio
        artwork.artist_nationality = metadata.artist_nationality
        artwork.artist_birth_year = metadata.artist_birth_year
        artwork.artist_death_year = metadata.artist_death_year
        
        # Dates
        artwork.date_display = metadata.date_display
        artwork.date_start = metadata.date_start
        artwork.date_end = metadata.date_end
        
        # Physical Details
        artwork.medium = metadata.medium
        artwork.dimensions = metadata.dimensions
        artwork.height_cm = metadata.height_cm
        artwork.width_cm = metadata.width_cm
        artwork.depth_cm = metadata.depth_cm
        artwork.diameter_cm = metadata.diameter_cm
        
        # Classification
        artwork.department = metadata.department
        artwork.artwork_type = metadata.artwork_type
        artwork.culture = metadata.culture
        artwork.style = metadata.style
        
        # Rights & Display
        artwork.is_public_domain = metadata.is_public_domain
        artwork.credit_line = metadata.credit_line
        artwork.is_on_view = metadata.is_on_view
        artwork.is_highlight = metadata.is_highlight
        artwork.is_boosted = metadata.is_boosted
        artwork.boost_rank = metadata.boost_rank
        artwork.has_not_been_viewed_much = metadata.has_not_been_viewed_much
        
        # Rich Content
        artwork.description = metadata.description
        artwork.short_description = metadata.short_description
        artwork.provenance = metadata.provenance
        artwork.inscriptions = metadata.inscriptions
        artwork.fun_fact = metadata.fun_fact
        artwork.style_titles = metadata.style_titles
        artwork.keywords = metadata.keywords
        
        # Images
        artwork.primary_image_url = metadata.primary_image_url
        artwork.image_urls = metadata.image_urls
        if image_path:
            artwork.image_path = str(image_path)
        
        # Analytics
        artwork.colorfulness = metadata.colorfulness
        artwork.color_h = metadata.color_h
        artwork.color_s = metadata.color_s
        artwork.color_l = metadata.color_l
        
        self.session.commit()
        return artwork
        
    def get_artwork(self, museum_code: str, original_id: str) -> Optional[Artwork]:
        """Get artwork by museum code and original ID"""
        return self.session.query(Artwork).join(Museum).filter(
            Museum.code == museum_code,
            Artwork.original_id == original_id
        ).first()