import os
from pathlib import Path
import logging
from typing import Dict, Any
import json
import re
from .config import settings

def get_project_root() -> Path:
    """Get the absolute path to the project root directory."""
    # return Path(__file__).parent.parent
    root = Path(__file__).parent.parent
    settings.initialize_paths(root)
    return root

def ensure_directory(path: Path) -> None:
    """Create directory if it doesn't exist."""
    path.mkdir(parents=True, exist_ok=True)

def sanitize_filename(id: str, title: str, artist: str, max_length: int = 255) -> str:
    """
    Sanitize and truncate filename, preserving AIC ID and artist name.
    
    Args:
        aic_id: Artwork ID number
        title: Artwork title
        artist: Artist name
        max_length: Maximum length for the filename (default: 255 for macOS)
    
    Returns:
        Sanitized filename with format: "{aic_id}_{truncated_title}_{artist}.jpg"
    """
    
    # Remove invalid characters from title and artist
    def clean_text(text: str) -> str:
        # Remove invalid filename characters
        text = re.sub(r'[<>:"/\\|?*]', '', text)
        # Collapse multiple spaces and remove newlines
        return ' '.join(text.split())

    # Clean the components
    clean_title = clean_text(title)
    clean_artist = clean_text(artist)
    
    
    # Calculate available space for title
    # Format will be: "{aic_id}_{title}_{artist}.jpg"
    extension_length = 4  # ".jpg"
    separators_length = 2  # Two underscores
    id_length = len(str(id))
    artist_length = len(clean_artist)
    
    # Calculate maximum title length
    max_title_length = max_length - (
        id_length +
        artist_length +
        extension_length +
        separators_length
    )
    
    # Truncate title if necessary
    if len(clean_title) > max_title_length:
        clean_title = clean_title[:max_title_length-3] + "..."
    
    # Construct final filename
    filename = f"{id}_{clean_title}_{clean_artist}.jpg"
    
    logging.debug(f"Sanitized filename: {filename} (length: {len(filename)})")
    
    return filename
