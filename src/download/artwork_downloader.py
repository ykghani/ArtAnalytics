from abc import ABC, abstractmethod
import logging
import time
from typing import Optional, Dict, List, Any
from pathlib import Path

from .api_client import AICApiClient
from .progress_tracker import ProgressTracker
from .image_processor import ImageProcessor
from ..utils import sanitize_filename

class ArtworkDownloader:
    """Main orchestrator for downloading artwork."""
    
    def __init__(
        self,
        api_client: AICApiClient,
        progress_tracker: ProgressTracker,
        image_processor: ImageProcessor,
        rate_limit_delay: float = 1.0,
        max_downloads: Optional[int] = None, 
        max_storage_gb: Optional[float] = None
    ):
        self.api_client = api_client
        self.progress_tracker = progress_tracker
        self.image_processor = image_processor
        self.rate_limit_delay = rate_limit_delay
        self.max_downloads = max_downloads
        self.max_storage_bytes = int(max_storage_gb * 1024 * 1024 * 1024) if max_storage_gb else None
        self._download_count = 0
        self._total_size_bytes = 0
    
    def _check_limits(self, new_size_bytes: int = 0) -> bool:
        '''
        Checks if downloading another image would exceed capactiy limits 
        '''
        if self.max_downloads and self._download_count >= self.max_downloads:
            logging.warning(f"Maximum download count ({self.max_downloads}) reached")
            return False

        if self.max_storage_bytes: 
            projected_total = self._total_size_bytes + new_size_bytes
            if projected_total > self.max_storage_bytes:
                logging.warning(
                    "Max storage limit reached."
                    f"({self._total_size_bytes / (1024**3):.2f}GB / {self.max_storage_bytes / (1024**3):.2f}GB)"
                )
                return False
        
        return True
        

    def download_artwork(self, aic_id: int, img_id: str, title: str, artist: str) -> None:
        """Download and save a single artwork."""
        artwork_info = f"[AIC ID: {aic_id} '{title} by {artist}]"
        try:
            if not img_id:
                raise ValueError("No image ID available")

            image_data = self.api_client.get_image(img_id)
            
            #Check size limits
            image_size = len(image_data)
            if not self._check_limits(image_size):
                self.progress_tracker.log_status(aic_id, "skipped", "Download limits reached")
                return
            
            filename = self._generate_filename(aic_id, title, artist)
            self.image_processor.save_image(image_data, filename)
            
            self._download_count += 1
            self._total_size_bytes += image_size
            
            self.progress_tracker.log_status(aic_id, "success")
            logging.info(f"Successfully processed and saved {aic_id} as '{filename}")
            
        except Exception as e:
            error_msg = f"Failed to process {artwork_info}: {str(e)}"
            logging.error(error_msg)
            self._handle_error(aic_id, str(e))

    def download_all_artwork(self, force_restart: bool = False) -> None:
        """Download all public domain artwork from Prints and Drawings department."""
        logging.info(
            f"Starting download_all_artwork process with limits: "
            f"max_downloads={self.max_downloads}, "
            f"max_storage_gb={self.max_storage_bytes/1024**3 if self.max_storage_bytes else 'None'}GB"
        )
        
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
                logging.debug(f"Skipping already processed artwork ID: {aic_id}")
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

    def _generate_filename(self, aic_id: int, title: str, artist: str) -> str: 
        '''
        Generates a sanitized filename for the artwork using the sanitize_filename utility
        '''
        return sanitize_filename(str(aic_id), title, artist)