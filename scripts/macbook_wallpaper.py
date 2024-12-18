import sys
from pathlib import Path
import sqlite3
import random
import subprocess
import logging
from pathlib import Path
from PIL import Image

sys.path.append(str(Path(__file__).parent.parent))
from src.displays import DisplayRatios
# from src.museums.schemas import ArtworkMetadata

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DB_PATH = PROJECT_ROOT / "data" / "artwork.db"

def get_random_artwork(db_path: Path) -> tuple[str, dict]:
    """Get random artwork path and metadata from database."""
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT image_path, title, artist, description 
                FROM artworks 
                WHERE image_path IS NOT NULL
                ORDER BY RANDOM() 
                LIMIT 1
            """)
            result = cursor.fetchone()
            if result:
                return result[0], {
                    'title': result[1] or 'Untitled',
                    'artist': result[2] or 'Unknown Artist',
                    'description': result[3] or ''
                }
            return None, {}
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return None, {}

def prepare_wallpaper(image_path: str, metadata: dict = None) -> str:
    """Prepare image for wallpaper with metadata."""
    try:
        with Image.open(image_path) as img:
            wallpaper_image = DisplayRatios.pad_image_for_display(
                img, 
                metadata=metadata or {},
                target_aspect_ratio=1.6,
                background_color=(0, 0, 0)
            )
            
            wallpaper_path = Path(image_path).parent / f"wallpaper_{Path(image_path).name}"
            wallpaper_image.save(wallpaper_path)
            return str(wallpaper_path)
    except Exception as e:
        logger.error(f"Failed to prepare wallpaper with metadata: {e}")
        logger.info("Falling back to original image")
        return image_path

def set_wallpaper(image_path: str) -> bool:
    """Set macOS wallpaper using osascript with forced refresh."""
    try:
        abs_path = str(Path(image_path).resolve())
        
        # Commands to change wallpaper and force refresh
        commands = [
            # Set the wallpaper
            'tell application "System Events" to tell every desktop to set picture to "%s"' % abs_path,
            # Force a refresh by toggling dark mode
            'tell application "System Events" to tell appearance preferences to set dark mode to not dark mode',
            'delay 0.1',
            'tell application "System Events" to tell appearance preferences to set dark mode to not dark mode'
        ]
        
        # Run each command
        for cmd in commands:
            result = subprocess.run([
                'osascript',
                '-e',
                cmd
            ], check=True, capture_output=True, text=True)
            
            if result.stderr:
                logger.error(f"Error in command '{cmd}': {result.stderr}")
                return False
        
        logger.info(f"Wallpaper successfully changed to: {Path(abs_path).name}")
        return True
        
    except subprocess.SubprocessError as e:
        logger.error(f"Failed to set wallpaper: {e}")
        return False

def main():
    db_path = DB_PATH
    
    if not db_path.exists():
        logger.error(f"Database not found at {db_path}")
        return

    image_path, metadata = get_random_artwork(db_path)
    if not image_path:
        logger.error("No valid artwork found in database")
        return
    
    wallpaper_path = prepare_wallpaper(image_path,  metadata)
        
    if set_wallpaper(wallpaper_path):
        logger.info(f"Successfully set wallpaper to {image_path}")
    
if __name__ == "__main__":
    main()