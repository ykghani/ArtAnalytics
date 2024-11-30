from PIL import Image
from io import BytesIO
from pathlib import Path
import logging
from typing import Protocol, runtime_checkable

from ..museums.schemas.artwork_metadata import ArtworkMetadata

@runtime_checkable
class FilenameGenerator(Protocol):
    """Protocol defining interface for filename generation"""
    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        """Generate a filename from artwork metadata"""
        ...

class ImageProcessor:
    """Handles image processing and saving with museum-specific filename generation."""
    
    def __init__(self, output_dir: Path, filename_generator: FilenameGenerator):
        """
        Initialize ImageProcessor with output directory and filename generator.
        
        Args:
            output_dir: Directory where processed images will be saved
            filename_generator: Object implementing generate_filename method
        """
        self.output_dir = output_dir
        self.filename_generator = filename_generator
        self._ensure_output_dir()
    
    def _ensure_output_dir(self) -> None:
        """Ensure output directory exists."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save_image(self, image_data: bytes, metadata: ArtworkMetadata) -> Path:
        """
        Process and save an image using museum-specific filename generation.
        
        Args:
            image_data: Raw image data in bytes
            metadata: Standardized artwork metadata
            
        Returns:
            Path to the saved image file
            
        Raises:
            IOError: If there's an error saving the image
            ValueError: If image data is invalid
        """
        try:
            # Generate filename using museum-specific generator
            filename = self.filename_generator.generate_filename(metadata)
            filepath = self.output_dir / filename
            
            # Process and save image
            image = Image.open(BytesIO(image_data))
            image.save(filepath, format='JPEG', quality=95)
            
            logging.debug(f"Successfully saved image: {filepath.name}")
            return filepath
            
        except (IOError, ValueError) as e:
            error_msg = f"Error saving image for artwork {metadata.id}: {str(e)}"
            logging.error(error_msg)
            raise IOError(error_msg) from e