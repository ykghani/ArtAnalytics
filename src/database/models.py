from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Museum(Base):
    __tablename__ = 'museums'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    code = Column(String(10), unique=True, nullable=False)  # e.g., 'met', 'aic'
    artworks = relationship("Artwork", back_populates="museum")
    
    def __repr__(self):
        return f"<Museum(id={self.id}, name='{self.name}', code='{self.code}')>"

class Artwork(Base):
    __tablename__ = 'artworks'
    
    id = Column(Integer, primary_key=True)
    museum_id = Column(Integer, ForeignKey('museums.id'), nullable=False)
    original_id = Column(String(50), nullable=False)  # ID from the source museum
    title = Column(String(500))
    artist = Column(String(500))
    artist_display = Column(String(500))
    date_created = Column(String(100))
    medium = Column(String(500))
    dimensions = Column(String(500))
    credit_line = Column(String(1000))
    department = Column(String(100))
    is_public_domain = Column(Boolean, default=False)
    is_highlight = Column(Boolean, default=False)
    image_path = Column(String(1000))  # Local filepath to the image
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    museum = relationship("Museum", back_populates="artworks")
    
    # Composite unique constraint
    __table_args__ = (
        UniqueConstraint('museum_id', 'original_id', name='unique_artwork_per_museum'),
        Index('ix_artwork_title', 'title'),
        Index('ix_artwork_artist', 'artist'),
    )
    
    def __repr__(self):
        return f"<Artwork(id={self.id}, title='{self.title}', artist='{self.artist}')>"
