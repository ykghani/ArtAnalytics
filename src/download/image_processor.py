from PIL import Image
from io import BytesIO
from pathlib import Path
import logging

class ImageProcessor:
    """Handles image processing and saving."""
    
    def __init__(self, output_folder: Path):
        self.output_folder = output_folder

    def save_image(self, image_data: bytes, filename: str) -> None:
        """Process and save an image."""
        try: 
            image = Image.open(BytesIO(image_data))
            filepath = self.output_folder / filename
            image.save(filepath)
            logging.info(f"Successfully saved image: {filename}")
        except Exception as e: 
            logging.error(f"Error saving image {filename}: {str(e)}")
            raise