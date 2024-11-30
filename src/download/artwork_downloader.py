import logging
import time
from typing import Optional, Dict, List, Any
from pathlib import Path
import requests

from ..museums.base import MuseumAPIClient, MuseumImageProcessor
from ..museums.aic import AICProgressTracker
from ..museums.met import MetProgressTracker
from ..museums.cma import CMAProgressTracker
from ..museums.schemas import ArtworkMetadata
from .progress_tracker import BaseProgressTracker, ProgressState
from ..config import Settings
from ..database.database import Database
from ..database.models import Base
from ..database.repository import ArtworkRepository

class ArtworkDownloader:
    """Generic artwork downloader that works with any museum API client."""
    
    def __init__(
        self,
        client: MuseumAPIClient,
        image_processor: MuseumImageProcessor,
        progress_tracker: BaseProgressTracker,
        settings = Settings 
    ):
        self.client = client
        self.image_processor = image_processor
        self.progress_tracker = progress_tracker
        self.rate_limit_delay = 1 / settings.rate_limit_delay if settings.rate_limit_delay > 0 else 1.0
        self.max_retries = settings.max_retries
        self.error_rate_delay = settings.error_retry_delay
        self.batch_size = settings.batch_size
        self.max_downloads = settings.max_downloads
        self.max_storage_bytes = int(settings.max_storage_gb * 1024 * 1024 * 1024) if settings.max_storage_gb else None
        
        self._retry_count = 0
        self._download_count = 0
        self._total_size_bytes = 0
        
        self.db = Database(settings.database_path)
        self.db.create_tables()
        session = self.db.get_session()
        self.db.init_museums(session)
        self.artwork_repo = ArtworkRepository(session)
        
        
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
    
    def _should_retry(self, error: Exception) -> bool:
        """Determine if we should retry the download based on error type and retry count."""
        if self._retry_count >= self.max_retries:
            return False
            
        retriable_errors = (
            "timeout", "connection error", "network", 
            "500", "502", "503", "504"
        )
        
        error_str = str(error).lower()
        return any(err in error_str for err in retriable_errors)

    def download_artwork(self, artwork_metadata: ArtworkMetadata) -> None:
        """Download and process a single artwork."""
        try:
            artwork_info = f"{self.client.museum_info.name} ID: {artwork_metadata.id} '{artwork_metadata.title}' by '{artwork_metadata.artist}'"
            logging.info(f"Processing {artwork_info}")  # New log

            # Check if artwork exists in database
            existing_artwork = self.artwork_repo.get_artwork(
                self.client.museum_info.code, 
                artwork_metadata.id
            )
            
            if existing_artwork and existing_artwork.image_path:
                logging.info(f"Artwork {artwork_info} already exists in database")
                self.progress_tracker.log_status(artwork_metadata.id, "skipped", "Already in database")
                return
            
            if not artwork_metadata.is_public_domain:
                logging.info(f"Skipping {artwork_info} - not public domain")  # New log
                self.progress_tracker.log_status(artwork_metadata.id, "skipped", "Not public domain")
                return
            
            # Log image URL info
            logging.info(f"Image URL: {artwork_metadata.primary_image_url}")  # New log
            if not artwork_metadata.primary_image_url and not artwork_metadata.image_id:
                logging.info(f"No image URL available for {artwork_info}")  # New log
                self.progress_tracker.log_status(artwork_metadata.id, 'skipped', 'No image available')
                return

            # Get image data
            image_data = None
            if artwork_metadata.primary_image_url:
                response = self.client.session.get(artwork_metadata.primary_image_url)
                response.raise_for_status()
                image_data = response.content
            elif artwork_metadata.image_id:
                image_url = self.client.build_image_url(artwork_metadata.image_id)
                response = self.client.session.get(image_url)
                response.raise_for_status()
                image_data = response.content
                logging.info(f"Successfully downloaded image data: {len(image_data)} bytes")  # New log
            
        except Exception as e:
            error_msg = f'Failed to process {artwork_info}: {str(e)}'
            logging.error(error_msg)
            self._handle_error(artwork_metadata.id, str(e))


    def download_collection(self, params: Dict[str, Any]) -> None:
        """Download all artwork matching the given parameters."""
        logging.info(f'Starting collection download for {self.client.museum_info.name}')
        consecutive_errors = 0
        max_consecutive_errors = 10
        process_completed = False
        completion_reason = "Unknown"

        try:
            artwork_iterator = self.client.iter_collection(**params)
            
            while True:
                try:
                    # Get next artwork
                    try:
                        artwork = next(artwork_iterator)
                        
                        # Update AIC-specific page tracking if applicable
                        if isinstance(self.progress_tracker, AICProgressTracker) and hasattr(artwork, 'page'):
                            self.progress_tracker.update_page(artwork.page)
                        # Update Met-specific total objects if applicable
                        elif isinstance(self.progress_tracker, MetProgressTracker):
                            if hasattr(artwork, 'total_objects'):
                                self.progress_tracker.state.total_objects = artwork.total_objects
                            self.progress_tracker.state.last_object_id = artwork.id
                        elif isinstance(self.progress_tracker, CMAProgressTracker):
                            if hasattr(artwork, 'total_objects'):
                                self.progress_tracker.state.total_objects = artwork.total_objects
                            self.progress_tracker.state.last_object_id = artwork.id
                            
                    except StopIteration:
                        process_completed = True
                        completion_reason = 'Reached end of collection'
                        logging.info('No more art to process - reached end of collection')
                        break
                    
                    # Skip if already processed
                    if self.progress_tracker.is_processed(artwork.id):
                        logging.debug(f'Skipping already processed art ID: {artwork.id}')
                        continue
                    
                    # Download artwork
                    try:
                        self.download_artwork(artwork)
                        consecutive_errors = 0
                    except requests.exceptions.HTTPError as e:
                        consecutive_errors += 1
                        logging.error(f"HTTP Error downloading artwork {artwork.id}: {str(e)}")
                        
                        if e.response.status_code == 400:
                            process_completed = True
                            completion_reason = 'Reached invalid resource - likely end of collection'
                            break
                        
                        if consecutive_errors >= max_consecutive_errors:
                            completion_reason = f'Exceeded max consecutive errors {max_consecutive_errors}'
                            break
                        
                        time.sleep(self.error_rate_delay)
                        
                    except Exception as e:
                        consecutive_errors += 1
                        logging.error(f"Error downloading artwork {artwork.id}: {e}")
                        
                        if consecutive_errors >= max_consecutive_errors:
                            logging.error("Too many consecutive errors, stopping download")
                            completion_reason = 'Too many consecutive errors'
                            break
                        
                        time.sleep(self.error_rate_delay)
                    
                    time.sleep(self.rate_limit_delay)
                
                except Exception as e:
                    consecutive_errors += 1
                    logging.error(f"Error in main download loop: {e}")
                    
                    if consecutive_errors >= max_consecutive_errors:
                        logging.error("Too many consecutive errors, stopping download")
                        completion_reason = 'Too many consecutive errors in main loop'
                        break
                    
                    time.sleep(self.error_rate_delay)
        
        except Exception as e:
            logging.error(f"Fatal error in collection download: {e}")
            completion_reason = f'Fatal error: {str(e)}'
            raise
        
        finally:
            # Generate and log summary regardless of how we exit
            summary = self._generate_summary_report()
            self._log_summary(summary)

    def _process_page(self, artworks: List[Dict[str, Any]]) -> None:
        """Process a page of artwork data."""
        for art in artworks:
            artwork_id = str(art['id'])
            
            if self.progress_tracker.is_processed(artwork_id):
                logging.debug(f"Skipping already processed artwork ID: {artwork_id}")
                continue
            
            self.download_artwork(artwork_id)

    def _handle_error(self, aic_id: int, error_message: str) -> None:
        """Handle download errors with improved categorization."""
        error_type = self._categorize_error(error_message)
        self.progress_tracker.log_status(aic_id, error_type, error_message)
        
    def _categorize_error(self, error_message: str) -> str:
        """Categorize errors based on error message content."""
        error_message = error_message.lower()
        
        if any(term in error_message for term in ["timeout", "connection", "network"]):
            return "network_error"
        elif any(term in error_message for term in ["image", "jpg", "jpeg", "png"]):
            return "image_processing_error"
        elif any(term in error_message for term in ["valid", "schema", "format"]):
            return "validation_error"
        elif any(term in error_message for term in ["download", "fetch", "retrieve"]):
            return "download_error"
        elif "skip" in error_message:
            return "skipped"
        else:
            return "other_error"
    
    def _generate_summary_report(self) -> Dict[str, Any]:
        """Generate summary statistics for the download process."""
        stats = self.progress_tracker.get_statistics()
        
        total_size_gb = self._total_size_bytes / (1024**3)
        avg_size_mb = (self._total_size_bytes / max(stats['successful'], 1)) / (1024**2)
        
        success_rate = 0
        if stats['total_processed'] > 0:
            success_rate = (stats['successful'] / stats['total_processed']) * 100
        
        return {
            'total_processed': stats['total_processed'],
            'successful_downloads': stats['successful'],
            'failed_downloads': stats['failed'],
            'success_rate': f"{success_rate:.2f}%",
            'total_storage_used': f"{total_size_gb:.2f}GB",
            'average_file_size': f"{avg_size_mb:.2f}MB",
            'error_count': stats['error_count']
        }

    def _log_summary(self, summary: Dict[str, Any]) -> None:
        """Log summary report in a readable format."""
        logging.info("=" * 50)
        logging.info("DOWNLOAD PROCESS SUMMARY")
        logging.info("=" * 50)
        logging.info(f"Total Artworks Processed: {summary['total_processed']}")
        logging.info(f"Successful Downloads: {summary['successful_downloads']}")
        logging.info(f"Failed Downloads: {summary['failed_downloads']}")
        logging.info(f"Success Rate: {summary['success_rate']}")
        logging.info(f"Total Storage Used: {summary['total_storage_used']}")
        logging.info(f"Average File Size: {summary['average_file_size']}")
        logging.info(f"Error Count: {summary['error_count']}")
        
        if summary.get('error_breakdown'):
            logging.info("\nError Breakdown:")
            for error_type, count in summary['error_breakdown'].items():
                logging.info(f"  {error_type}: {count} occurrences")
        
        logging.info("=" * 50)