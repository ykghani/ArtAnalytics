# src/database/__init__.py

"""
Database package for the Art Analytics project.

This package provides database models and utilities for storing and managing
artwork metadata from various museum collections.

Key Components:
    - Database: Main database connection and session management
    - ArtworkRepository: Repository pattern implementation for artwork operations
    - Models: SQLAlchemy models (Museum, Artwork)

Example:
    from src.database import Database, ArtworkRepository
    
    db = Database(settings.database_path)
    db.create_tables()
    
    with db.get_session() as session:
        repo = ArtworkRepository(session)
        artwork = repo.get_artwork('met', '123456')
"""
from .database import Database
from .models import Base, Museum, Artwork
from .repository import ArtworkRepository

__all__ = ['Database', 'Base', 'Museum', 'Artwork', 'ArtworkRepository']