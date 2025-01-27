import logging
import time
from typing import Optional, Dict, List, Any
from pathlib import Path
import requests

from ..config import Settings, settings
from ..database.database import Database
from ..database.models import Base
from ..database.repository import ArtworkRepository
from ..museums.base import MuseumAPIClient, MuseumImageProcessor
from ..museums.aic import AICProgressTracker
from ..museums.met import MetProgressTracker
from ..museums.cma import CMAProgressTracker
from ..museums.schemas import ArtworkMetadata
from .progress_tracker import BaseProgressTracker, ProgressState
from ..utils import setup_logging

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
        self.logger = setup_logging(settings.logs_dir, settings.log_level, None)
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
    
    def _get_image_data(self, artwork_metadata: ArtworkMetadata) -> Optional[bytes]:
        """Get image data for artwork"""
        try:
            # Check for primary image URL first
            if artwork_metadata.primary_image_url:
                time.sleep(self.rate_limit_delay)
                response = self.client.session.get(artwork_metadata.primary_image_url)
                response.raise_for_status()
                return response.content
                
            # Legacy/compatibility for AIC image_id if needed
            elif hasattr(artwork_metadata, 'image_id') and artwork_metadata.image_id:
                url = self.client.build_image_url(artwork_metadata.image_id)
                response = self.client.session.get(url)
                response.raise_for_status()
                return response.content
                
            return None
            
        except Exception as e:
            self.logger.error(f"Error downloading image: {e}")
            return None

    def download_artwork(self, artwork_metadata: ArtworkMetadata) -> None:
        """Download and process a single artwork."""
        try:
            artwork_info = f"{self.client.museum_info.name} ID: {artwork_metadata.id} '{artwork_metadata.title}' by '{artwork_metadata.artist}"
            
            # Check if artwork exists in database
            existing_artwork = self.artwork_repo.get_artwork(
                self.client.museum_info.code, 
                artwork_metadata.id
            )
            
            if existing_artwork and existing_artwork.image_path:
                self.logger.debug(f"Artwork {artwork_info} already exists in database")
                self.progress_tracker.log_status(artwork_metadata.id, "skipped", "Already in database")
                return
                
            if not artwork_metadata.is_public_domain:
                self.logger.debug("Skipping non-public domain artwork")
                self.progress_tracker.log_status(artwork_metadata.id, "skipped", "Not public domain")
                return

            # Process artwork
            if image_data := self._get_image_data(artwork_metadata):
                image_size = len(image_data)
                if not self._check_limits(image_size):
                    self.logger.progress("Download limits reached")
                    self.progress_tracker.log_status(artwork_metadata.id, 'skipped', 'Download limits reached')
                    return
                
                image_path = self.image_processor.process_image(image_data, artwork_metadata)
                
                # Save to database
                self.artwork_repo.create_or_update_artwork(
                    metadata=artwork_metadata,
                    museum_code=self.client.museum_info.code,
                    image_path=str(image_path)
                )
                
                self._update_download_stats(image_size)
                self.progress_tracker.log_status(artwork_metadata.id, 'success')
                self.logger.artwork(f'Successfully processed artwork: {artwork_info}')
            else:
                self.logger.debug(f"No image available for {artwork_info}")
                self.progress_tracker.log_status(artwork_metadata.id, 'skipped', 'No image available')
                return
                
        except Exception as e:
            error_msg = f'Failed to process {artwork_info}: {str(e)}'
            self.logger.error(error_msg)
            self._handle_error(artwork_metadata.id, str(e))
    
    def _update_download_stats(self, size_bytes: int) -> None:
        """Update download statistics."""
        self._download_count += 1
        self._total_size_bytes += size_bytes

    def download_collection(self, params: Dict[str, Any]) -> None:
        """Download all artwork matching the given parameters."""    
        self.logger.progress(f'Starting collection download for {self.client.museum_info.name}')
        consecutive_errors = 0
        max_consecutive_errors = 10

        try:
            artwork_iterator = self.client.iter_collection(**params)
            
            while True:
                try:
                    artwork = next(artwork_iterator, None)
                    if artwork is None:
                        self.logger.progress('Reached end of collection')
                        break
                    
                    if self.progress_tracker.is_processed(artwork.id):
                        self.logger.debug(f'Skipping processed art ID: {artwork.id}')
                        continue
                    
                    try:
                        self.download_artwork(artwork)
                        consecutive_errors = 0
                    except requests.exceptions.HTTPError as e:
                        consecutive_errors += 1
                        self.logger.error(f"HTTP Error downloading artwork {artwork.id}: {str(e)}")
                        
                        if consecutive_errors >= max_consecutive_errors:
                            self.logger.error("Exceeded max consecutive errors")
                            break
                        
                        time.sleep(self.error_rate_delay)
                        
                except Exception as e:
                    consecutive_errors += 1
                    self.logger.error(f"Error in main download loop: {e}")
                    
                    if consecutive_errors >= max_consecutive_errors:
                        self.logger.error("Too many consecutive errors, stopping download")
                        break
                    
                    time.sleep(self.error_rate_delay)
        
        finally:
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
        self.logger.progress("=" * 50)
        self.logger.progress("DOWNLOAD PROCESS SUMMARY")
        self.logger.progress("=" * 50)
        self.logger.progress(f"Total Artworks Processed: {summary['total_processed']}")
        self.logger.progress(f"Successful Downloads: {summary['successful_downloads']}")
        self.logger.progress(f"Failed Downloads: {summary['failed_downloads']}")
        self.logger.progress(f"Success Rate: {summary['success_rate']}")
        self.logger.progress(f"Total Storage Used: {summary['total_storage_used']}")
        self.logger.progress(f"Average File Size: {summary['average_file_size']}")
        self.logger.progress(f"Error Count: {summary['error_count']}")
        
        if summary.get('error_breakdown'):
            self.logger.progress("\nError Breakdown:")
            for error_type, count in summary['error_breakdown'].items():
                self.logger.progress(f"  {error_type}: {count} occurrences")
        
        self.logger.progress("=" * 50)