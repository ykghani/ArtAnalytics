import sys
from pathlib import Path
import logging
from typing import Tuple

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from src.config import settings, LogLevel
from src.utils import setup_logging  # Import the existing logging setup

def parse_filename(filename: str) -> Tuple[str, str, str]:
    """Parse the components of the original filename."""
    # Remove .jpg extension
    base_name = filename.rsplit('.', 1)[0]
    
    # Split on first and last underscore
    try:
        id_part, middle = base_name.split('_', 1)
        title, artist = middle.rsplit('_', 1)
        return id_part, title, artist
    except ValueError as e:
        logging.error(f"Failed to parse filename: {filename}")
        raise ValueError(f"Invalid filename format: {filename}") from e

def create_new_filename(museum_id: str, id: str, title: str, artist: str, 
                       max_length: int = 255) -> str:
    """Create new filename with format '{museum_id}_{id}_{title}_{artist}.jpg'."""
    # Calculate space needed for fixed components
    extension_len = 4  # ".jpg"
    separators_len = 3  # Three underscores
    fixed_len = len(museum_id) + len(id) + len(artist) + extension_len + separators_len
    
    # Calculate and enforce maximum title length
    max_title_len = max_length - fixed_len
    if len(title) > max_title_len:
        title = title[:max_title_len-3] + "..."
        
    return f"{museum_id}_{id}_{title}_{artist}.jpg"

def main():
    # Initialize settings with project root
    settings.initialize_paths(project_root)
    
    # Use the existing logging setup
    setup_logging()
    
    try:
        images_dir = settings.IMAGES_DIR
        if not images_dir or not images_dir.exists():
            raise ValueError(f"Images directory not found: {images_dir}")
            
        # Get list of jpg files
        image_files = list(images_dir.glob("*.jpg"))
        logging.info(f"Found {len(image_files)} images to process")
        
        # Process each image
        success_count = 0
        error_count = 0
        
        for image_path in image_files:
            try:
                # Parse current filename
                id_part, title, artist = parse_filename(image_path.name)
                
                # Create new filename
                new_filename = create_new_filename("AIC", id_part, title, artist)
                new_path = image_path.parent / new_filename
                
                # Skip if file already exists with new name
                if new_path.exists():
                    logging.warning(f"File already exists, skipping: {new_filename}")
                    continue
                
                # Rename file
                image_path.rename(new_path)
                success_count += 1
                
                if success_count % 100 == 0:
                    logging.info(f"Processed {success_count} files...")
                    
            except Exception as e:
                logging.error(f"Error processing {image_path.name}: {str(e)}")
                error_count += 1
                continue
        
        logging.info(f"Rename process complete. "
                    f"Successfully renamed {success_count} files. "
                    f"Encountered {error_count} errors.")
            
    except Exception as e:
        logging.error(f"Script failed: {str(e)}")
        raise

if __name__ == "__main__":
    main()