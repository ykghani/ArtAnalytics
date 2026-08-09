import os
from pathlib import Path
import logging
from typing import Dict, Any, Optional, Tuple
from enum import Enum
from io import BytesIO
import json
import re

import requests
from PIL import Image

from .log_level import LogLevel

ARTWORK = 15  # Between DEBUG (10) and INFO (20)
PROGRESS = 25  # Between INFO (20) and WARNING (30)

logging.addLevelName(ARTWORK, "ARTWORK")
logging.addLevelName(PROGRESS, "PROGRESS")


# Add convenience methods
def artwork(self, message, *args, **kwargs):
    self.log(ARTWORK, message, *args, **kwargs)


def progress(self, message, *args, **kwargs):
    self.log(PROGRESS, message, *args, **kwargs)


logging.Logger.artwork = artwork
logging.Logger.progress = progress


def setup_logging(
    log_dir: Path, log_level: LogLevel, museum_code: Optional[str] = None
) -> logging.Logger:
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
        LogLevel.DEBUG: logging.DEBUG,
    }

    if log_level != LogLevel.NONE:
        # File handler specific to this logger
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(file_handler)

        # Add console handler for non-museum loggers
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
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
        text = re.sub(r'[<>:"/\\|?*]', "", text)
        # Collapse multiple spaces and remove newlines
        return " ".join(text.split())

    # Clean the components
    clean_title = clean_text(title)
    clean_artist = clean_text(artist)

    def byte_len(text: str) -> int:
        return len(text.encode("utf-8"))

    def truncate_to_byte_length(text: str, max_bytes: int) -> str:
        # Truncate by character, not byte slice, to avoid splitting a
        # multi-byte UTF-8 character in half.
        while byte_len(text) > max_bytes and text:
            text = text[:-1]
        return text

    # Calculate available space for title
    # Format will be: "{aic_id}_{title}_{artist}.jpg"
    # max_length is a byte limit (filesystems cap filenames at 255 bytes),
    # so all lengths below must be measured in encoded UTF-8 bytes, not
    # characters — an accented title can be well under 255 characters but
    # over 255 bytes once umlauts etc. are encoded.
    extension_length = 4  # ".jpg"
    separators_length = 2  # Two underscores
    id_length = byte_len(str(id))
    artist_length = byte_len(clean_artist)

    # Calculate maximum title length
    max_title_length = max_length - (
        id_length + artist_length + extension_length + separators_length
    )

    # Truncate title if necessary
    if byte_len(clean_title) > max_title_length:
        clean_title = truncate_to_byte_length(clean_title, max(max_title_length - 3, 0)) + "..."

    # Construct final filename
    filename = f"{id}_{clean_title}_{clean_artist}.jpg"

    logging.debug(f"Sanitized filename: {filename} (length: {byte_len(filename)} bytes)")

    return filename


def fetch_remote_image_dimensions(
    image_url: str, timeout: float = 10.0
) -> Optional[Tuple[int, int]]:
    """Get an image's pixel (width, height) without downloading it in full.

    Streams just enough of the response for PIL to parse the format header
    (JPEG/PNG/WEBP all declare dimensions within the first few KB, but some
    files carry enough EXIF/metadata to push that further in) — a small
    initial read is tried first, then a larger one, before giving up.

    For museums whose API doesn't return pixel dimensions directly, this is
    the metadata-only-safe way to populate ArtworkMetadata.image_pixel_width/
    image_pixel_height for quality scoring — request only a rendition sized
    for on-screen display (not an archival master), and only read its header.

    Returns None if the dimensions can't be determined (network error,
    truncated read too small to parse, etc.) — callers should treat that as
    "no dimensions available" rather than raise.
    """
    for max_bytes in (131072, 524288):  # 128KB, then 512KB
        resp = None
        image = None
        try:
            resp = requests.get(image_url, stream=True, timeout=timeout)
            resp.raise_for_status()

            buffer = BytesIO()
            for chunk in resp.iter_content(chunk_size=32768):
                if not chunk:
                    continue
                buffer.write(chunk)
                if buffer.tell() >= max_bytes:
                    break
            buffer.seek(0)

            image = Image.open(buffer)
            return image.size
        except Exception:
            continue
        finally:
            if image is not None:
                try:
                    image.close()
                except Exception:
                    pass
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass

    return None
