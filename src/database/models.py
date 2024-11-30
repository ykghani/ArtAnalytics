from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Museum(Base):
    __tablename__ = 'museums'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    code = Column(String(10), unique=True, nullable=False)
    artworks = relationship("Artwork", back_populates="museum")

class Artwork(Base):
    __tablename__ = 'artworks'
    
    id = Column(Integer, primary_key=True)
    museum_id = Column(Integer, ForeignKey('museums.id'), nullable=False)
    original_id = Column(String(50), nullable=False)
    accession_number = Column(String(50))
    
    # Basic Artwork Info
    title = Column(String(500))
    artist = Column(String(500))
    artist_display = Column(String(500))
    artist_bio = Column(String(1000))
    artist_nationality = Column(String(100))
    artist_birth_year = Column(Integer)
    artist_death_year = Column(Integer)
    
    # Dates
    date_display = Column(String(100))
    date_start = Column(DateTime)
    date_end = Column(DateTime)
    
    # Physical Details
    medium = Column(String(500))
    dimensions = Column(String(500))
    height_cm = Column(Float)
    width_cm = Column(Float)
    depth_cm = Column(Float)
    diameter_cm = Column(Float)
    
    # Classification
    department = Column(String(100))
    artwork_type = Column(String(100))
    culture = Column(String(200))
    style = Column(String(100))
    
    # Rights & Display
    is_public_domain = Column(Boolean, default=False)
    credit_line = Column(String(1000))
    is_on_view = Column(Boolean)
    is_highlight = Column(Boolean)
    is_boosted = Column(Boolean)
    boost_rank = Column(Integer)
    has_not_been_viewed_much = Column(Boolean)
    
    # Rich Content
    description = Column(String(5000))
    short_description = Column(String(1000))
    provenance = Column(String(5000))
    inscriptions = Column(String(1000))
    fun_fact = Column(String(1000))
    
    # Images
    primary_image_url = Column(String(1000))
    
    # Analytics
    colorfulness = Column(Float)
    color_h = Column(Integer)
    color_s = Column(Integer)
    color_l = Column(Integer)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    museum = relationship("Museum", back_populates="artworks")
    
    __table_args__ = (
        UniqueConstraint('museum_id', 'original_id', name='unique_artwork_per_museum'),
    )