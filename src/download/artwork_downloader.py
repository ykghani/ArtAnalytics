import logging
import time
from typing import Optional, Dict, List, Any
from pathlib import Path
import requests

from ..museums.base import MuseumAPIClient, MuseumImageProcessor
from ..museums.schemas import ArtworkMetadata
from .progress_tracker import ProgressTracker
from ..config import Settings

class ArtworkDownloader:
    """Generic artwork downloader that works with any museum API client."""
    
    def __init__(
        self,
        client: MuseumAPIClient,
        image_processor: MuseumImageProcessor,
        progress_tracker: ProgressTracker,
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
            artwork_info = f"{self.client.museum_info.name} ID: {artwork_metadata.id} '{artwork_metadata.title}' by '{artwork_metadata.artist}"
            
            if not artwork_metadata.is_public_domain:
                self.progress_tracker.log_status(artwork_metadata.id, "skipped", "Not public domain")
                return
            
            if artwork_metadata.primary_image_url:
                response = self.client.session.get(artwork_metadata.primary_image_url)
                response.raise_for_status()
                image_data = response.content
            elif artwork_metadata.image_id: #Aic approach 
                image_url = self.client.build_image_url(artwork_metadata.image_id)
                reponse = self.client.session.get(image_url)
                reponse.raise_for_status()
                image_data = response.content
            else:
                self.progress_tracker.log_status(artwork_metadata.id, 'skipped', 'No image available')
                return
            
            image_size = len(image_data)
            if not self._check_limits(image_size):
                self.progress_tracker.log_status(artwork_metadata.id, 'skipped', 'Download limits reached')
                return
            
            self.image_processor.process_image(image_data, artwork_metadata)
            
            self._download_count += 1
            self._total_size_bytes += image_size
            
            self.progress_tracker.log_status(artwork_metadata.id, 'success')
            logging.info(f'Successfully processed {artwork_info}')
            
        except Exception as e: 
            error_msg = f'Failed to process {artwork_info}: {str(e)}'
            logging.error(error_msg)
            self._handle_error(artwork_metadata.id, str(e))
        
        finally:
            time.sleep(self.rate_limit_delay)
        
        
        # try:
        #     # Get standardized metadata
        #     metadata = self.client.get_artwork_details(artwork_id)
        #     artwork_info = f"[{self.client.museum_info.name} ID: {artwork_id}] '{metadata.title}' by {metadata.artist}"
            
        #     # Check if we can process this artwork
        #     if not metadata.is_public_domain:
        #         self.progress_tracker.log_status(artwork_id, "skipped", "Not in public domain")
        #         return
                
        #     if not metadata.image_id:
        #         self.progress_tracker.log_status(artwork_id, "skipped", "No image available")
        #         return

        #     # Download image
        #     image_url = self.client.build_image_url(metadata.image_id)
        #     response = self.client.session.get(image_url)
        #     response.raise_for_status()
            
        #     # Check size limits
        #     image_size = len(response.content)
        #     if not self._check_limits(image_size):
        #         self.progress_tracker.log_status(artwork_id, "skipped", "Download limits reached")
        #         return
            
        #     # Process and save image
        #     self.image_processor.process_image(response.content, metadata)
            
        #     self._download_count += 1
        #     self._total_size_bytes += image_size
            
        #     self.progress_tracker.log_status(artwork_id, "success")
        #     logging.info(f"Successfully processed {artwork_info}")
            
        # except Exception as e:
        #     error_msg = f"Failed to process {artwork_info}: {str(e)}"
        #     logging.error(error_msg)
        #     self._handle_error(artwork_id, str(e))
            
        # finally:
        #     time.sleep(self.rate_limit_delay)


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
                    try:
                        artwork = next(artwork_iterator)
                    except StopIteration:
                        process_completed = True
                        completion_reason = 'Reached end of collection'
                        logging.info(f'No more art to process - reached end of collection')
                        break
                    
                    if self.progress_tracker.is_processed(artwork.id):
                        logging.debug(f'Skipping already processed art ID: {artwork.id}')
                        continue
                    
                    #Download artwork 
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
                            break
                        
                        time.sleep(self.error_rate_delay)
                    
                    time.sleep(self.rate_limit_delay)
                
                except Exception as e:
                    consecutive_errors += 1
                    logging.error(f"Error downloading artwork {artwork.id}: {e}")
                    
                    if consecutive_errors >= max_consecutive_errors:
                        logging.error("Too many consecutive errors, stopping download")
                        break
                    
                    time.sleep(self.error_rate_delay)
                
                time.sleep(self.rate_limit_delay)
            
        finally:
            # Generate and log summary regardless of how we exit
            summary = self._generate_summary_report()
            self._log_summary(summary)
            
            if process_completed:
                logging.info(f"Download process completed: {completion_reason}")
            else:
                logging.warning(f"Download process ended before completion: {completion_reason}")

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
        total_processed = len(self.progress_tracker.download_log['all'])
        successful = len(self.progress_tracker.download_log['success'])
        failed = len(self.progress_tracker.download_log['failed'])
        
        total_size_gb = self._total_size_bytes / (1024**3)
        avg_size_mb = (self._total_size_bytes / max(successful, 1)) / (1024**2)
        
        return {
            'total_processed': total_processed,
            'successful_downloads': successful,
            'failed_downloads': failed,
            'success_rate': f"{(successful/max(total_processed, 1))*100:.2f}%",
            'total_storage_used': f"{total_size_gb:.2f}GB",
            'average_file_size': f"{avg_size_mb:.2f}MB",
            'last_page_processed': self.progress_tracker.get_last_page(),
            'error_breakdown': self.progress_tracker.download_log.get('other_error', {})
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
        logging.info(f"Last Page Processed: {summary['last_page_processed']}")
        
        if summary['error_breakdown']:
            logging.info("\nError Breakdown:")
            for error_type, errors in summary['error_breakdown'].items():
                logging.info(f"  {error_type}: {len(errors)} occurrences")
        
        logging.info("=" * 50)