from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.schema import UniqueConstraint

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
    
    # Basic Info
    title = Column(String(500))
    artist = Column(String(500))
    artist_display = Column(String(500))
    artist_bio = Column(Text)
    artist_nationality = Column(String(100))
    artist_birth_year = Column(Integer)
    artist_death_year = Column(Integer)
    
    # Dates
    date_display = Column(String(100))
    date_start = Column(String(50))
    date_end = Column(String(50))
    
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
    culture = Column(JSON)  # Store as JSON array
    style = Column(String(100))
    
    # Rights & Display
    is_public_domain = Column(Boolean, default=False)
    credit_line = Column(Text)
    is_on_view = Column(Boolean)
    is_highlight = Column(Boolean)
    is_boosted = Column(Boolean)
    boost_rank = Column(Integer)
    has_not_been_viewed_much = Column(Boolean)
    
    # Rich Content
    description = Column(Text)
    short_description = Column(Text)
    provenance = Column(Text)
    inscriptions = Column(JSON)  # Store as JSON array
    fun_fact = Column(Text)
    style_titles = Column(JSON)  # Store as JSON array
    keywords = Column(JSON)  # Store as JSON array
    
    # Images
    primary_image_url = Column(String(1000))
    image_urls = Column(JSON)  # Store as JSON object
    image_path = Column(String(1000))  # Local filepath
    
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