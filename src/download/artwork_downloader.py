import logging
import time
from typing import Optional, Dict, List, Any
from pathlib import Path
import requests

from ..museums.base import MuseumAPIClient, MuseumImageProcessor
from ..museums.schemas import ArtworkMetadata
from .progress_tracker import ProgressTracker

class ArtworkDownloader:
    """Generic artwork downloader that works with any museum API client."""
    
    def __init__(
        self,
        client: MuseumAPIClient,
        image_processor: MuseumImageProcessor,
        progress_tracker: ProgressTracker,
        max_downloads: Optional[int] = None,
        max_storage_gb: Optional[float] = None
    ):
        self.client = client
        self.image_processor = image_processor
        self.progress_tracker = progress_tracker
        self.max_downloads = max_downloads
        self.max_storage_bytes = int(max_storage_gb * 1024 * 1024 * 1024) if max_storage_gb else None
        self._download_count = 0
        self._total_size_bytes = 0
        
        # Use museum's configured rate limit
        self.rate_limit_delay = 1.0 / self.client.museum_info.rate_limit
        
    def _check_limits(self, new_size_bytes: int = 0) -> bool:
        """Check if downloading another image would exceed capacity limits."""
        if self.max_downloads and self._download_count >= self.max_downloads:
            logging.warning(f"Maximum download count ({self.max_downloads}) reached")
            return False

        if self.max_storage_bytes:
            projected_total = self._total_size_bytes + new_size_bytes
            if projected_total > self.max_storage_bytes:
                logging.warning(
                    f"Max storage limit reached. "
                    f"({self._total_size_bytes / (1024**3):.2f}GB / "
                    f"{self.max_storage_bytes / (1024**3):.2f}GB)"
                )
                return False
        
        return True

    def download_artwork(self, artwork_id: str) -> None:
        """Download and process a single artwork."""
        try:
            # Get standardized metadata
            metadata = self.client.get_artwork_details(artwork_id)
            artwork_info = f"[{self.client.museum_info.name} ID: {artwork_id}] '{metadata.title}' by {metadata.artist}"
            
            # Check if we can process this artwork
            if not metadata.is_public_domain:
                self.progress_tracker.log_status(artwork_id, "skipped", "Not in public domain")
                return
                
            if not metadata.image_id:
                self.progress_tracker.log_status(artwork_id, "skipped", "No image available")
                return

            # Download image
            image_url = self.client.build_image_url(metadata.image_id)
            response = self.client.session.get(image_url)
            response.raise_for_status()
            
            # Check size limits
            image_size = len(response.content)
            if not self._check_limits(image_size):
                self.progress_tracker.log_status(artwork_id, "skipped", "Download limits reached")
                return
            
            # Process and save image
            self.image_processor.process_image(response.content, metadata)
            
            self._download_count += 1
            self._total_size_bytes += image_size
            
            self.progress_tracker.log_status(artwork_id, "success")
            logging.info(f"Successfully processed {artwork_info}")
            
        except Exception as e:
            error_msg = f"Failed to process {artwork_info}: {str(e)}"
            logging.error(error_msg)
            self._handle_error(artwork_id, str(e))
            
        finally:
            time.sleep(self.rate_limit_delay)

    def download_collection(self, params: Dict[str, Any], force_restart: bool = False) -> None:
        """Download artworks from the collection matching given parameters."""
        museum_name = self.client.museum_info.name
        logging.info(
            f"Starting download process for {museum_name} with limits: "
            f"max_downloads={self.max_downloads}, "
            f"max_storage_gb={self.max_storage_bytes/1024**3 if self.max_storage_bytes else 'None'}GB"
        )
        
        page = 1 if force_restart else self.progress_tracker.get_last_page() + 1
        logging.info(f"Starting from page {page}")
        
        while True:
            try:
                logging.info(f"Fetching page {page}")
                data = self.client.get_artwork_page(page, params)
                
                if not data.get('data'):
                    logging.info("No more artwork to process")
                    break
                
                logging.info(f"Processing {len(data['data'])} artworks from page {page}")
                self._process_page(data['data'])
                self.progress_tracker.update_page(page)
                
                page += 1
                
            except Exception as e:
                logging.error(f"Error processing page {page}: {str(e)}")
                time.sleep(5)  # Longer delay on error
                continue

    def _process_page(self, artworks: List[Dict[str, Any]]) -> None:
        """Process a page of artwork data."""
        for art in artworks:
            artwork_id = str(art['id'])
            
            if self.progress_tracker.is_processed(artwork_id):
                logging.debug(f"Skipping already processed artwork ID: {artwork_id}")
                continue
            
            self.download_artwork(artwork_id)

    def _handle_error(self, artwork_id: str, error_message: str) -> None:
        """Handle download errors with appropriate categorization."""
        if "network" in error_message.lower():
            self.progress_tracker.log_status(artwork_id, "network_error", error_message)
        elif "image" in error_message.lower():
            self.progress_tracker.log_status(artwork_id, "image_processing_error", error_message)
        else:
            self.progress_tracker.log_status(artwork_id, "other_error", error_message)