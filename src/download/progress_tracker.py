import json
from pathlib import Path
from typing import Dict, Set, Union, List
import logging

class ProgressTracker:
    """Manages download progress tracking and reporting."""
    
    def __init__(self, progress_file: Path):
        """
        Initialize progress tracker.
        
        Args:
            progress_file (Path): Path to the progress tracking file
        """
        # Ensure we have a Path object
        self.progress_file = Path(progress_file) if not isinstance(progress_file, Path) else progress_file
        
        # Initialize tracking data
        self.download_log = {
            "success": [],
            "failed": [],
            "network_error": [],
            "image_processing_error": [],
            "other_error": {},
            "all": set(),
            "last_page": 0
        }
        
        self._load_progress()

    def _load_progress(self) -> None:
        """Load previously processed IDs from progress file."""
        try:
            if self.progress_file.exists():
                with self.progress_file.open('r') as f:
                    data = json.load(f)
                    self.download_log = data
                    self.download_log['all'] = set(data['all'])
                logging.info(f"Loaded progress file. {len(self.download_log['all'])} items previously processed")
            else:
                logging.info("No progress file found. Starting fresh")
                self._save_progress()
        except Exception as e:
            logging.error(f"Error loading progress file: {str(e)}. Starting fresh")
            self._save_progress()

    def _save_progress(self) -> None:
        """Save progress to file."""
        try:
            # Ensure directory exists
            self.progress_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Prepare data for serialization
            download_log_copy = {
                "success": self.download_log["success"],
                "failed": self.download_log["failed"],
                "network_error": self.download_log["network_error"],
                "image_processing_error": self.download_log["image_processing_error"],
                "other_error": self.download_log["other_error"],
                "all": list(self.download_log["all"]),  # Convert set to list
                "last_page": self.download_log["last_page"]
            }
            
            # Write to file atomically using temporary file
            temp_file = self.progress_file.with_suffix('.tmp')
            with temp_file.open('w') as f:
                json.dump(download_log_copy, f, indent=4)
            
            # Rename temporary file to target file
            temp_file.replace(self.progress_file)
            
        except Exception as e:
            logging.error(f"Error saving progress: {str(e)}")

    def log_status(self, artwork_id: str, status: str, error_message: str = None) -> None:
        """Log download status for an artwork."""
        artwork_id = str(artwork_id)  # Ensure ID is string
        
        if status == "success":
            self.download_log["success"].append(artwork_id)
        else:
            self.download_log["failed"].append(artwork_id)
            if error_message:
                if status not in self.download_log["other_error"]:
                    self.download_log["other_error"][status] = {}
                self.download_log["other_error"][status][artwork_id] = error_message
        
        self.download_log['all'].add(artwork_id)
        self._save_progress()

    def update_page(self, page: int) -> None:
        """Update the last processed page number."""
        self.download_log['last_page'] = page
        self._save_progress()

    def is_processed(self, artwork_id: str) -> bool:
        """Check if an artwork ID has already been processed."""
        return str(artwork_id) in self.download_log['all']

    def get_last_page(self) -> int:
        """Get the last processed page number."""
        return self.download_log['last_page']

    def get_statistics(self) -> Dict[str, int]:
        """Get download statistics."""
        return {
            "total_processed": len(self.download_log['all']),
            "successful": len(self.download_log['success']),
            "failed": len(self.download_log['failed']),
            "network_errors": len(self.download_log['network_error']),
            "image_errors": len(self.download_log['image_processing_error']),
            "other_errors": len(self.download_log['other_error'])
        }