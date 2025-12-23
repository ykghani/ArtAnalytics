import sqlite3
import subprocess
import logging
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import textwrap
from typing import Optional, Tuple
import tempfile
import os
import time
from datetime import datetime
import sys
import requests

from src.utils import setup_logging
from src.log_level import LogLevel


# Constants
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
LOG_PATH = PROJECT_ROOT / 'data' / 'logs'
DB_PATH = PROJECT_ROOT / "data" / "artwork.db"

# Display settings for MacBook Pro 14" (3456×2234)
DISPLAY_WIDTH = 3024
DISPLAY_HEIGHT = 1964
TEXT_AREA_HEIGHT = 120  # Reserved space for metadata text

logger = setup_logging(log_dir= LOG_PATH,
                       log_level= LogLevel.DEBUG,
                       museum_code= 'wallpaper')

logger.debug(f"Script started at: {datetime.now()}")
logger.debug(f"Python executable: {sys.executable}")
logger.debug(f"Working directory: {os.getcwd()}")
logger.debug(f"DISPLAY env var: {os.environ.get('DISPLAY')}")

def build_iiif_url(image_id: str, width: Optional[int] = None, height: Optional[int] = None) -> str:
    """
    Build IIIF Image API URL for AIC artwork.

    Args:
        image_id: AIC image identifier
        width: Desired width in pixels (height auto-calculated if only width specified)
        height: Desired height in pixels (width auto-calculated if only height specified)

    Returns:
        IIIF URL string

    Examples:
        build_iiif_url('abc123', width=3456) -> requests image at 3456px width
        build_iiif_url('abc123', height=2234) -> requests image at 2234px height
        build_iiif_url('abc123') -> requests maximum available size
    """
    base_url = "https://www.artic.edu/iiif/2"

    if width and height:
        # Request exact dimensions (may distort aspect ratio)
        size = f"{width},{height}"
    elif width:
        # Width-constrained, height auto-calculated
        size = f"{width},"
    elif height:
        # Height-constrained, width auto-calculated
        size = f",{height}"
    else:
        # Maximum available size
        size = "max"

    return f"{base_url}/{image_id}/full/{size}/0/default.jpg"

def calculate_text_dimensions(metadata: dict, font_title, font_info, max_width: int) -> tuple[int, int]:
    """
    Calculate required text box dimensions.
    
    Returns:
        Tuple of (width, height) in pixels
    """
    # Create temporary image for text measurements
    temp_draw = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    padding = 20
    line_spacing = 10
    
    # Safely get and wrap text, defaulting to empty string
    title = textwrap.fill(str(metadata.get('title') or ''), width=40)
    artist = textwrap.fill(str(metadata.get('artist_display') or 
                             metadata.get('artist') or ''), width=50)
    
    # Get text bounding boxes
    title_bbox = temp_draw.textbbox((0, 0), title, font=font_title)
    artist_bbox = temp_draw.textbbox((0, 0), artist, font=font_info)
    
    # Calculate total dimensions
    total_width = max(title_bbox[2], artist_bbox[2]) + (padding * 2)
    total_height = (title_bbox[3] + artist_bbox[3] + line_spacing + (padding * 2))
    
    return total_width, total_height

def prepare_wallpaper(image: Image.Image, metadata: dict, target_width: int = DISPLAY_WIDTH,
                      target_height: int = DISPLAY_HEIGHT) -> Image.Image:
    """
    Prepare image with metadata for wallpaper display.

    This function assumes the image is already sized appropriately via IIIF.
    It handles final fitting to exact display dimensions and adds metadata text.

    Args:
        image: PIL Image (should be pre-sized via IIIF)
        metadata: Dict with artwork metadata
        target_width: Final wallpaper width in pixels
        target_height: Final wallpaper height in pixels

    Returns:
        PIL Image at exact target dimensions with metadata text
    """
    metadata = metadata or {}

    try:
        font_title = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
        font_info = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
    except:
        font_title = ImageFont.load_default()
        font_info = ImageFont.load_default()

    # Calculate available space for artwork (reserve space for text)
    artwork_height = target_height - TEXT_AREA_HEIGHT

    # Calculate scaling to ensure image fills a good portion of the screen
    # Target: fill at least 70% of available width OR height (whichever is limiting)
    min_fill_ratio = 0.7
    img_width, img_height = image.size
    img_aspect = img_width / img_height
    available_aspect = target_width / artwork_height

    # Determine optimal size based on aspect ratio
    if img_aspect > available_aspect:
        # Image is wider - scale to fill width
        new_width = max(int(target_width * min_fill_ratio), img_width)
        new_height = int(new_width / img_aspect)
    else:
        # Image is taller - scale to fill height
        new_height = max(int(artwork_height * min_fill_ratio), img_height)
        new_width = int(new_height * img_aspect)

    # Ensure we don't exceed available space
    if new_width > target_width or new_height > artwork_height:
        # Scale down to fit
        image.thumbnail((target_width, artwork_height), Image.Resampling.LANCZOS)
    else:
        # Scale up for better visibility (using high-quality resampling)
        image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

    logger.debug(f"Final image size: {image.width}x{image.height} (available: {target_width}x{artwork_height})")

    # Create canvas at exact display dimensions
    wallpaper = Image.new('RGB', (target_width, target_height), (0, 0, 0))

    # Center image both horizontally and vertically in artwork area
    # This avoids the notch and keeps artwork in the "safe zone"
    x_offset = (target_width - image.width) // 2
    y_offset = (artwork_height - image.height) // 2
    wallpaper.paste(image, (x_offset, y_offset))

    logger.debug(f"Image positioned at offset: ({x_offset}, {y_offset})")

    # Add metadata text in reserved space below artwork area
    draw = ImageDraw.Draw(wallpaper)
    padding = 20
    text_y = artwork_height + padding

    # Draw title
    if metadata.get('title'):
        title = textwrap.fill(metadata['title'], width=60)
        draw.text((padding, text_y), title, font=font_title, fill=(255, 255, 255))
        title_bbox = draw.textbbox((padding, text_y), title, font=font_title)
        text_y = title_bbox[3] + 8

    # Draw artist
    if metadata.get('artist_display'):
        artist = textwrap.fill(metadata['artist_display'], width=70)
        draw.text((padding, text_y), artist, font=font_info, fill=(200, 200, 200))

    return wallpaper

def get_random_artwork(db_path: Path) -> Tuple[Optional[str], Optional[dict], Optional[str]]:
    """
    Get random artwork from database with IIIF support.

    Returns:
        Tuple of (image_path, metadata_dict, image_id)
        image_path: Local file path (fallback if IIIF fails)
        metadata_dict: Artwork metadata
        image_id: AIC image_id for IIIF URL construction (extracted from image_urls JSON)
    """
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            # Get artwork with image_urls JSON field
            cursor.execute("""
                SELECT image_path, title, artist, artist_display, description, image_urls, original_id
                FROM artworks
                WHERE image_path IS NOT NULL
                ORDER BY RANDOM()
                LIMIT 1
            """)
            result = cursor.fetchone()
            if result:
                # Extract image_id from IIIF URL in image_urls JSON
                image_id = None
                if result[5]:  # image_urls field
                    import json
                    try:
                        image_urls = json.loads(result[5])
                        # Extract image_id from any IIIF URL (e.g., "https://www.artic.edu/iiif/2/{image_id}/full/...")
                        for url in image_urls.values():
                            if 'artic.edu/iiif/2/' in url:
                                image_id = url.split('/iiif/2/')[1].split('/')[0]
                                break
                    except (json.JSONDecodeError, IndexError, AttributeError):
                        logger.debug("Could not extract image_id from image_urls")

                return result[0], {
                    'title': result[1],
                    'artist': result[2],
                    'artist_display': result[3],
                    'description': result[4]
                }, image_id
            return None, None, None
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return None, None, None

def set_wallpaper(image_path: str) -> bool:
    """Set macOS wallpaper using osascript with enhanced error checking."""
    try:
        # Verify file exists and is readable
        if not Path(image_path).exists():
            logger.error(f"Wallpaper file not found: {image_path}")
            return False

        # Construct the AppleScript command
        script = f'''tell application "System Events"
            tell every desktop
                set picture to "{image_path}"
            end tell
        end tell'''
        
        # Run the command and capture output
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            check=False  # Don't raise exception on non-zero exit
        )
        
        # Log detailed output
        if result.returncode != 0:
            logger.error(f"osascript failed with return code {result.returncode}")
            logger.error(f"Error output: {result.stderr}")
            return False
            
        if result.stdout:
            logger.debug(f"osascript output: {result.stdout}")
            
        return True

    except subprocess.SubprocessError as e:
        logger.error(f"Failed to set wallpaper: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error setting wallpaper: {str(e)}")
        return False

def load_image_from_iiif(image_id: str, width: int) -> Optional[Image.Image]:
    """
    Load image from AIC IIIF server at specified width.

    Args:
        image_id: AIC image identifier
        width: Desired image width in pixels

    Returns:
        PIL Image or None if download fails
    """
    url = build_iiif_url(image_id, width=width)
    logger.debug(f"Fetching image from IIIF: {url}")

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        # Check if image is too small (IIIF returns smaller if source is unavailable at requested size)
        from io import BytesIO
        img = Image.open(BytesIO(response.content))
        logger.info(f"Downloaded image: {img.width}x{img.height} (requested width: {width})")

        # Warn if significantly smaller than requested
        if img.width < width * 0.7:
            logger.warning(f"Downloaded image is smaller than requested: {img.width}px vs {width}px")

        return img
    except Exception as e:
        logger.error(f"Failed to load image from IIIF: {e}")
        return None

def main():
    start_time = time.time()
    logger.info(f"Starting wallpaper change at {datetime.now()}")

    if not DB_PATH.exists():
        logger.error(f"Database not found at {DB_PATH}")
        return

    # Get random artwork
    image_path, metadata, image_id = get_random_artwork(DB_PATH)
    if not image_path or not metadata:
        logger.error("No valid artwork found in database")
        return

    logger.info(f"Selected artwork: {metadata.get('title', 'Unknown')} by {metadata.get('artist', 'Unknown')}")

    temp_path = None
    try:
        # Create temporary file
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_file:
            temp_path = tmp_file.name
            logger.debug(f"Created temporary file: {temp_path}")

            # Try IIIF first, fall back to local file
            img = None
            if image_id:
                logger.info("Attempting to load high-resolution image via IIIF...")
                img = load_image_from_iiif(image_id, width=DISPLAY_WIDTH)

            if img is None:
                logger.info("Using local image file...")
                img = Image.open(image_path)

            # Process and save wallpaper
            processed = prepare_wallpaper(img, metadata)
            processed.save(temp_path, 'JPEG', quality=95)
            img.close()

            # Verify temp file exists and is readable
            temp_file_path = Path(temp_path)
            if not temp_file_path.exists():
                logger.error(f"Temporary file not created: {temp_path}")
                return

            if not os.access(temp_path, os.R_OK):
                logger.error(f"Temporary file not readable: {temp_path}")
                return

            logger.debug(f"Temporary file size: {temp_file_path.stat().st_size} bytes")

            # Set wallpaper
            if set_wallpaper(temp_path):
                logger.info(f"Successfully set wallpaper: {metadata.get('title', 'Unknown')}")
            else:
                logger.error("Failed to set wallpaper")

    except Exception as e:
        logger.error(f"Error processing wallpaper: {e}")
        import traceback
        logger.debug(traceback.format_exc())
    finally:
        # Clean up temporary file after a delay
        try:
            # Wait a bit to ensure the system has read the file
            time.sleep(2)
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
                logger.debug(f"Cleaned up temporary file: {temp_path}")
        except Exception as e:
            logger.error(f"Error cleaning up temporary file: {e}")

    end_time = time.time()
    duration = end_time - start_time
    logger.info(f"Wallpaper change completed in {duration:.2f} seconds")

if __name__ == "__main__":
    main()