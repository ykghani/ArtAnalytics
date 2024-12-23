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

from src.utils import setup_logging
from src.log_level import LogLevel


# Constants
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
LOG_PATH = PROJECT_ROOT / 'data' / 'logs'
DB_PATH = PROJECT_ROOT / "data" / "artwork.db"

logger = setup_logging(log_dir= LOG_PATH,
                       log_level= LogLevel.DEBUG,
                       museum_code= 'wallpaper')

logger.debug(f"Script started at: {datetime.now()}")
logger.debug(f"Python executable: {sys.executable}")
logger.debug(f"Working directory: {os.getcwd()}")
logger.debug(f"DISPLAY env var: {os.environ.get('DISPLAY')}")

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

def prepare_wallpaper(image: Image.Image, metadata: dict) -> Image.Image:
    """Prepare image with metadata for wallpaper display."""
    # Ensure metadata is a dict
    metadata = metadata or {}
    
    try:
        font_title = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
        font_info = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
    except:
        font_title = ImageFont.load_default()
        font_info = ImageFont.load_default()

    # Get required text dimensions
    text_width, text_height = calculate_text_dimensions(metadata, font_title, font_info, image.width)
    
    # Calculate padding needed for aspect ratio and text
    target_ratio = 16/10
    orig_width, orig_height = image.size
    current_ratio = orig_width / orig_height
    
    # Calculate new dimensions ensuring space for text
    if current_ratio > target_ratio:
        new_height = max(
            int(orig_width / target_ratio),  # Height needed for aspect ratio
            orig_height + text_height  # Height needed for text
        )
        new_width = orig_width
    else:
        new_height = orig_height + text_height
        new_width = max(
            int(orig_height * target_ratio),  # Width needed for aspect ratio
            orig_width  # Original width
        )

    # Create padded image
    padded = Image.new('RGB', (new_width, new_height), (0, 0, 0))
    
    # Center original image at top
    x_offset = (new_width - orig_width) // 2
    padded.paste(image, (x_offset, 0))
    
    # Add metadata text
    draw = ImageDraw.Draw(padded)
    padding = 20
    text_y = orig_height + padding  # Start text below image
    
    # Draw title
    if metadata.get('title'):
        title = textwrap.fill(metadata['title'], width=40)
        draw.text((padding, text_y), title, font=font_title, fill=(255, 255, 255))
        title_bbox = draw.textbbox((padding, text_y), title, font=font_title)
        text_y = title_bbox[3] + 10  # Add spacing
        
    # Draw artist
    if metadata.get('artist_display'):
        artist = textwrap.fill(metadata['artist_display'], width=50)
        draw.text((padding, text_y), artist, font=font_info, fill=(255, 255, 255))

    return padded

def get_random_artwork(db_path: Path) -> Tuple[Optional[str], Optional[dict]]:
    """Get random artwork path and metadata from database."""
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT image_path, title, artist, artist_display, description 
                FROM artworks 
                WHERE image_path IS NOT NULL
                ORDER BY RANDOM() 
                LIMIT 1
            """)
            result = cursor.fetchone()
            if result:
                return result[0], {
                    'title': result[1],
                    'artist': result[2],
                    'artist_display': result[3],
                    'description': result[4]
                }
            return None, None
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return None, None

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

def main():
    start_time = time.time()
    logger.info(f"Starting wallpaper change at {datetime.now()}")

    if not DB_PATH.exists():
        logger.error(f"Database not found at {DB_PATH}")
        return

    # Get random artwork
    image_path, metadata = get_random_artwork(DB_PATH)
    if not image_path:
        logger.error("No valid artwork found in database")
        return

    try:
        # Create temporary file
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_file:
            temp_path = tmp_file.name
            logger.debug(f"Created temporary file: {temp_path}")

            # Process image
            with Image.open(image_path) as img:
                processed = prepare_wallpaper(img, metadata)
                processed.save(temp_path, 'JPEG', quality=95)
            
            # Verify temp file exists and is readable
            temp_file = Path(temp_path)
            if not temp_file.exists():
                logger.error(f"Temporary file not created: {temp_path}")
                return
                
            if not os.access(temp_path, os.R_OK):
                logger.error(f"Temporary file not readable: {temp_path}")
                return
                
            logger.debug(f"Temporary file size: {temp_file.stat().st_size} bytes")

            # Set wallpaper
            if set_wallpaper(temp_path):
                logger.info(f"Successfully set wallpaper from {image_path}")
            else:
                logger.error("Failed to set wallpaper")

    except Exception as e:
        logger.error(f"Error processing wallpaper: {e}")
    finally:
        # Clean up temporary file after a delay
        try:
            # Wait a bit to ensure the system has read the file
            time.sleep(2)
            os.unlink(temp_path)
            logger.debug(f"Cleaned up temporary file: {temp_path}")
        except Exception as e:
            logger.error(f"Error cleaning up temporary file: {e}")

    end_time = time.time()
    duration = end_time - start_time
    logger.info(f"Wallpaper change completed in {duration:.2f} seconds")

if __name__ == "__main__":
    main()