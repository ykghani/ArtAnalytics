import logging
import time
from typing import Optional, Dict, List, Any

from .api_client import AICApiClient
from .progress_tracker import ProgressTracker
from .image_processor import ImageProcessor

class ArtworkDownloader:
    """Main orchestrator for downloading artwork."""
    
    def __init__(
        self,
        api_client: AICApiClient,
        progress_tracker: ProgressTracker,
        image_processor: ImageProcessor,
        rate_limit_delay: float = 1.0
    ):
        self.api_client = api_client
        self.progress_tracker = progress_tracker
        self.image_processor = image_processor
        self.rate_limit_delay = rate_limit_delay

    def download_artwork(self, aic_id: int, img_id: str, title: str, artist: str) -> None:
        """Download and save a single artwork."""
        try:
            if not img_id:
                raise ValueError("No image ID available")

            image_data = self.api_client.get_image(img_id)
            filename = self._generate_filename(aic_id, title, artist)
            
            self.image_processor.save_image(image_data, filename)
            self.progress_tracker.log_status(aic_id, "success")
            
        except Exception as e:
            self._handle_error(aic_id, str(e))

    def download_all_artwork(self, force_restart: bool = False) -> None:
        """Download all public domain artwork from Prints and Drawings department."""
        logging.info("Starting download_all_artwork process")
        params = {
            'is_public_domain': 'true',
            'department_title': 'Prints and Drawings',
            'fields': 'id,title,artist_display,image_id,department_title',
            'limit': 100
        }
        
        page = 1 if force_restart else self.progress_tracker.get_last_page() + 1
        logging.info(f"Starting from page {page}")
        
        while True:
            try:
                logging.info(f"Fetching page {page}")
                data = self.api_client.get_artwork_page(page, params)
                
                if not data.get('data'):
                    logging.info("No more artwork to process")
                    break
                
                logging.info(f"Processing {len(data['data'])} artworks from page {page}")
                self._process_page(data['data'])
                self.progress_tracker.update_page(page)
                
                page += 1
                time.sleep(self.rate_limit_delay)
                
            except Exception as e:
                logging.error(f"Error processing page {page}: {str(e)}")
                time.sleep(5)  # Longer delay on error
                continue

    def _process_page(self, artworks: List[Dict[str, Any]]) -> None:
        """Process a page of artwork data."""
        for art in artworks:
            aic_id = art['id']
            
            if self.progress_tracker.is_processed(aic_id):
                continue
                
            if art['department_title'] != 'Prints and Drawings':
                self.progress_tracker.log_status(aic_id, "skipped", "Not in Prints and Drawings department")
                continue
                
            self.download_artwork(
                aic_id,
                art.get('image_id'),
                art.get('title', 'Untitled'),
                art.get('artist_display', 'Unknown Artist')
            )
            time.sleep(self.rate_limit_delay)

    def _handle_error(self, aic_id: int, error_message: str) -> None:
        """Handle download errors."""
        if "network" in error_message.lower():
            self.progress_tracker.log_status(aic_id, "network_error", error_message)
        elif "image" in error_message.lower():
            self.progress_tracker.log_status(aic_id, "image_processing_error", error_message)
        else:
            self.progress_tracker.log_status(aic_id, "other_error", error_message)