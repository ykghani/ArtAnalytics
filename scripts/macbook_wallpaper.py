import sqlite3
import random
import subprocess
import logging
from pathlib import Path

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DB_PATH = PROJECT_ROOT / "data" / "artwork.db"

def get_random_artwork(db_path: Path) -> str:
    """Get random artwork path from database."""
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT image_path FROM artworks 
                WHERE image_path IS NOT NULL
                ORDER BY RANDOM() 
                LIMIT 1
            """)
            result = cursor.fetchone()
            return result[0] if result else None
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return None

def set_wallpaper(image_path: str) -> bool:
    """Set macOS wallpaper using osascript."""
    try:
        cmd = [
            'osascript', 
            '-e', 
            f'tell application "Finder" to set desktop picture to POSIX file "{image_path}"'
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.SubprocessError as e:
        logger.error(f"Failed to set wallpaper: {e}")
        return False

def main():
    if not DB_PATH.exists():
        logger.error(f"Database not found at {DB_PATH}")
        return
    
    db_path = DB_PATH
    
    if not db_path.exists():
        logger.error(f"Database not found at {db_path}")
        return

    image_path = get_random_artwork(db_path)
    if not image_path:
        logger.error("No valid artwork found in database")
        return
        
    if set_wallpaper(image_path):
        logger.info(f"Successfully set wallpaper to {image_path}")
    
if __name__ == "__main__":
    main()