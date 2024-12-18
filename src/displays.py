from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
import textwrap

@dataclass
class DisplayRatios:
    """Common display aspect ratios"""
    PHONE = 0.46  # 9:19.5
    TABLET = 0.75       # 3:4
    MACBOOK = 1.6     # 16:10
    MONITOR = 1.77    # 16:9
    
    
    @staticmethod
    def get_suitable_displays(aspect_ratio: float, tolerance: float = 0.1) -> List[str]:
        """Get list of suitable displays for a given aspect ratio"""
        RATIO_RANGES = {
            'phone': (0.45, 0.48),    # iPhone-like
            'tablet': (0.70, 0.80),   # iPad-like
            'laptop': (1.5, 1.7),     # MacBook-like
            'monitor': (1.6, 1.8)     # Standard monitors
            }
        
        return [
            display for display, (min_ratio, max_ratio) in RATIO_RANGES.items()
            if min_ratio - tolerance <= aspect_ratio <= max_ratio + tolerance
        ]
    
    @staticmethod
    def pad_image_for_display(
        image: Image.Image, 
        metadata: Optional['ArtworkMetadata'] = None,
        max_width: int = 3456,  # MacBook Pro 16" width
        max_height: int = 2234,  # MacBook Pro 16" height
        target_aspect_ratio: float = 1.6,
        background_color: Tuple[int, int, int] = (255, 255, 255),
        text_color: Tuple[int, int, int] = (0, 0, 0),
        metadata_padding: int = 40  # Additional padding for metadata
    ) -> Image.Image:
        """
        Comprehensive image preparation for display
        
        Args:
            image: Source image
            metadata: Optional artwork metadata
            max_width: Maximum display width
            max_height: Maximum display height
            target_aspect_ratio: Desired display aspect ratio
            background_color: RGB background color
            text_color: Color for metadata text
            metadata_padding: Extra padding for metadata text
        
        Returns:
            Prepared image with optional metadata
        """
        # Validate inputs
        if not isinstance(image, Image.Image):
            raise ValueError("Invalid image input")

        original_width, original_height = image.size
        current_ratio = original_width / original_height

        # Downscaling logic
        if original_width > max_width or original_height > max_height:
            # Calculate scaling factor
            width_scale = max_width / original_width
            height_scale = max_height / original_height
            scale = min(width_scale, height_scale)
            
            new_width = int(original_width * scale)
            new_height = int(original_height * scale)
            
            # High-quality downscaling
            image = image.resize((new_width, new_height), Image.LANCZOS)
            original_width, original_height = new_width, new_height
            current_ratio = original_width / original_height

        # Prepare for metadata rendering
        metadata_height = 0
        if metadata:
            # Estimate metadata text height
            try:
                # Use a default font to estimate text height
                font_title = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
                font_artist = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
                font_description = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
                
                # Estimate text heights
                title = metadata.title or "Untitled"
                artist = metadata.artist_display or metadata.artist or "Unknown Artist"
                description = metadata.short_description or metadata.description or ""
                
                # Use a consistent method to estimate text height
                draw = ImageDraw.Draw(Image.new('RGB', (1, 1)))
                title_height = draw.textbbox((0, 0), title, font=font_title)[3]
                artist_height = draw.textbbox((0, 0), artist, font=font_artist)[3]
                description_height = draw.textbbox((0, 0), description, font=font_description)[3]
                
                metadata_height = title_height + artist_height + description_height + (3 * metadata_padding)
            except Exception:
                metadata_height = 200  # Fallback estimate

        # Padding logic with metadata consideration
        if abs(current_ratio - target_aspect_ratio) > 0.1 or metadata_height > 0:
            # Add extra vertical space for metadata if needed
            additional_vertical_space = metadata_height if metadata_height > 0 else 0
            
            if current_ratio > target_aspect_ratio:
                # Image is wider, pad vertically
                new_height = int(original_width / target_aspect_ratio) + additional_vertical_space
                padded = Image.new('RGB', (original_width, new_height), background_color)
                vertical_offset = (new_height - original_height - additional_vertical_space) // 2
                padded.paste(image, (0, vertical_offset))
            else:
                # Image is taller, pad horizontally
                new_width = int(original_height * target_aspect_ratio) + additional_vertical_space
                padded = Image.new('RGB', (new_width, original_height), background_color)
                horizontal_offset = (new_width - original_width) // 2
                padded.paste(image, (horizontal_offset, 0))
        else:
            padded = image.copy()

        # Render metadata if available
        if metadata:
            padded = DisplayRatios.render_artwork_metadata(
                padded, 
                metadata, 
                background_color=background_color, 
                text_color=text_color
            )

        return padded
    
    @staticmethod
    def calculate_aspect_ratio(width: float, height: float) -> float:
        '''Calculate aspect ratio '''
        return width / height
    
    @classmethod
    def analyze_image(cls, image_path: str) -> dict:
        '''
        Comprehensive image analysis
        
        Args:
            image_path: Path to the image file
        
        Returns:
            Dictionary with image analysis details
        '''
        with Image.open(image_path) as img:
            width, height = img.size
            aspect_ratio = cls.calculate_aspect_ratio(width, height)
            
            return {
                'width': width,
                'height': height,
                'aspect_ratio': aspect_ratio,
                'suitable_displays': cls.get_suitable_displays(aspect_ratio)
            }
    
    @staticmethod
    def render_artwork_metadata(
        image: Image.Image, 
        metadata: dict,
        background_color: Tuple[int, int, int] = (255, 255, 255),
        text_color: Tuple[int, int, int] = (0, 0, 0)
    ) -> Image.Image:
        """
        Render artwork metadata on padded image
        
        Args:
            image: Padded base image
            metadata: Artwork metadata object
            background_color: Background color for padding
            text_color: Color for metadata text
        
        Returns:
            Image with metadata rendered
        """
        # Validate metadata
        if not DisplayRatios._validate_metadata(metadata):
            return image
        
        # Create a copy to avoid modifying original
        result = image.copy()
        draw = ImageDraw.Draw(result)
        
        # Load a font (adjust path as needed)
        try:
            # Try system fonts, fallback to a default
            try:
                font_title = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
                font_artist = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
                font_description = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
            except IOError:
                font_title = ImageFont.load_default()
                font_artist = ImageFont.load_default()
                font_description = ImageFont.load_default()
        except Exception:
            return image
        
        # Calculate padding and text placement
        padding = 20
        text_area_width = result.width - (2 * padding)
        
        # Prepare text with line wrapping
        title = metadata.get('title', 'Untitled')
        artist = metadata.get('artist', 'Unknown Artist')
        description = metadata.get('description', '')
        
        # Wrap text to fit within image width
        wrapped_title = textwrap.fill(title, width=30)
        wrapped_artist = textwrap.fill(artist, width=40)
        wrapped_description = textwrap.fill(description, width=50)
        
        # Calculate text sizes
        title_bbox = draw.textbbox((0, 0), wrapped_title, font=font_title)
        artist_bbox = draw.textbbox((0, 0), wrapped_artist, font=font_artist)
        description_bbox = draw.textbbox((0, 0), wrapped_description, font=font_description)
        
        # Calculate total text height
        total_text_height = (
            title_bbox[3] - title_bbox[1] + 
            artist_bbox[3] - artist_bbox[1] + 
            description_bbox[3] - description_bbox[1] + 
            (2 * padding)  # Additional spacing between text blocks
        )
        
        # Ensure text fits in padded area
        if total_text_height > result.height / 2:
            return image
        
        # Text positioning (bottom left)
        y_offset = result.height - total_text_height
        
        # Render texts
        draw.text(
            (padding, y_offset), 
            wrapped_title, 
            font=font_title, 
            fill=text_color
        )
        
        y_offset += title_bbox[3] - title_bbox[1] + padding
        draw.text(
            (padding, y_offset), 
            wrapped_artist, 
            font=font_artist, 
            fill=text_color
        )
        
        y_offset += artist_bbox[3] - artist_bbox[1] + padding
        draw.text(
            (padding, y_offset), 
            wrapped_description, 
            font=font_description, 
            fill=text_color
        )
        
        return result
    
    @staticmethod
    def _validate_metadata(metadata: dict) -> bool:
        """Validate metadata for rendering"""
        if not metadata or not isinstance(metadata, dict):
            return False
        
        # Check if any of the key fields exist
        text_fields = [
            metadata.get('title'),
            metadata.get('artist'),
            metadata.get('description')
        ]
        
        return any(text_fields)