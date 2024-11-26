from typing import Optional
from sqlalchemy.orm import Session
from .models import Artwork, Museum
from ..museums.schemas import ArtworkMetadata

class ArtworkRepository:
    def __init__(self, session: Session):
        self.session = session
    
    def create_or_update_artwork(self, metadata: ArtworkMetadata, museum_code: str, image_path: Optional[str] = None) -> Artwork:
        """Create or update artwork record"""
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
        
        # Update artwork attributes
        artwork.title = metadata.title
        artwork.artist = metadata.artist
        artwork.artist_display = metadata.artist_display
        artwork.date_created = metadata.date_created
        artwork.medium = metadata.medium
        artwork.dimensions = metadata.dimensions
        artwork.credit_line = metadata.credit_line
        artwork.department = metadata.department
        artwork.is_public_domain = metadata.is_public_domain
        artwork.is_highlight = metadata.is_highlight
        
        if image_path:
            artwork.image_path = str(image_path)
        
        self.session.commit()
        return artwork
        
    def get_artwork(self, museum_code: str, original_id: str) -> Optional[Artwork]:
        """Get artwork by museum code and original ID"""
        return self.session.query(Artwork).join(Museum).filter(
            Museum.code == museum_code,
            Artwork.original_id == original_id
        ).first()