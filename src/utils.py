import os
from pathlib import Path
import logging
from typing import Dict, Any, Optional
from enum import Enum
import json
import re

from .log_level import LogLevel

ARTWORK = 15  # Between DEBUG (10) and INFO (20)
PROGRESS = 25  # Between INFO (20) and WARNING (30)

logging.addLevelName(ARTWORK, 'ARTWORK')
logging.addLevelName(PROGRESS, 'PROGRESS')

# Add convenience methods
def artwork(self, message, *args, **kwargs):
    self.log(ARTWORK, message, *args, **kwargs)

def progress(self, message, *args, **kwargs):
    self.log(PROGRESS, message, *args, **kwargs)


logging.Logger.artwork = artwork
logging.Logger.progress = progress

def setup_logging(log_dir: Path, log_level: LogLevel, museum_code: Optional[str] = None) -> logging.Logger:
    """Configure logging with both program-level and museum-specific logs.
    
    Args:
        log_dir: Directory where log files will be stored
        log_level: LogLevel enum specifying logging verbosity
        museum_code: Optional museum code for museum-specific logging
        
    Returns:
        Logger instance configured for the specified context
    """
    from .config import settings
    # Create logs directory if it doesn't exist
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Get the appropriate logger
    if museum_code:
        logger = logging.getLogger(f"museum.{museum_code}")
        log_file = log_dir / f"{museum_code}_downloader.log"
    else:
        logger = logging.getLogger("artwork_downloader")  # Root program logger
        log_file = log_dir / "artwork_downloader.log"
    
    # Clear any existing handlers
    logger.handlers = []
    
    # Set propagation based on type
    logger.propagate = museum_code is not None  # Museum loggers propagate to root
    
    # Map log levels
    level_map = {
        LogLevel.NONE: logging.CRITICAL + 1,
        LogLevel.ERRORS_ONLY: logging.ERROR,
        LogLevel.PROGRESS: PROGRESS,
        LogLevel.ARTWORK: ARTWORK,
        LogLevel.DEBUG: logging.DEBUG
    }
    
    if log_level != LogLevel.NONE:
        # File handler specific to this logger
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        )
        logger.addHandler(file_handler)
        
        # Add console handler for non-museum loggers
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(
            logging.Formatter('%(levelname)s - %(message)s'))
        logger.addHandler(console_handler)
    
    logger.setLevel(level_map.get(log_level, logging.INFO))
    return logger


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
    if not id:
        raise ValueError("ID cannot be None or empty")
    if not title:
        title = "Untitled"
    if not artist:
        artist = "Unknown"
    
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