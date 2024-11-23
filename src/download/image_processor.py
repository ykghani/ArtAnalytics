from PIL import Image
from io import BytesIO
from pathlib import Path
import logging
from ..museums.schemas import ArtworkMetadata

class ImageProcessor:
    """Handles image processing and saving."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save_image(self, image_data: bytes, metadata: ArtworkMetadata) -> None:
        """Process and save an image using standardized metadata."""
        try:
            image = Image.open(BytesIO(image_data))
            filepath = self.output_dir / self._generate_filename(metadata)
            image.save(filepath)
            logging.debug(f"Successfully saved image: {filepath.name}")
        except Exception as e:
            logging.error(f"Error saving image for artwork {metadata.id}: {str(e)}")
            raise
            
    def _generate_filename(self, metadata: ArtworkMetadata) -> str:
        """Generate a filename from artwork metadata."""
        from ..utils import sanitize_filename
        return sanitize_filename(metadata)