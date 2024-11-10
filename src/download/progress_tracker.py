# progress_tracker.py
import json
from pathlib import Path
from typing import Dict, Set, Union, List
import logging

class ProgressTracker:
    """Manages download progress tracking and reporting."""
    
    def __init__(self, progress_file: Path):
        self.progress_file = progress_file
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
        if self.progress_file.exists():
            try:
                with open(self.progress_file, 'r') as f:
                    data = json.load(f)
                    self.download_log = data
                    self.download_log['all'] = set(data['all'])
                logging.info(f"Loaded progress file. {len(self.download_log['all'])} items previously processed")
            except json.JSONDecodeError:
                logging.warning("Error reading progress file. Starting fresh")
                self._save_progress()
        else:
            logging.info("No progress file found. Starting fresh")
            self._save_progress()

    def _save_progress(self) -> None:
        """Save progress to file."""
        download_log_copy = self.download_log.copy()
        download_log_copy['all'] = list(download_log_copy['all'])
        
        with open(self.progress_file, 'w') as f:
            json.dump(download_log_copy, f, indent=4)

    def log_status(self, aic_id: int, status: str, error_message: str = None) -> None:
        """Log download status and save progress."""
        if status == "success":
            self.download_log["success"].append(aic_id)
        else:
            self.download_log["failed"].append(aic_id)
            if error_message:
                if status not in self.download_log["other_error"]:
                    self.download_log["other_error"][status] = {}
                self.download_log["other_error"][status][str(aic_id)] = error_message
        
        self.download_log['all'].add(aic_id)
        self._save_progress()

    def update_page(self, page: int) -> None:
        """Update the last processed page number."""
        self.download_log['last_page'] = page
        self._save_progress()

    def is_processed(self, aic_id: int) -> bool:
        """Check if an artwork ID has already been processed."""
        return aic_id in self.download_log['all']

    def get_last_page(self) -> int:
        """Get the last processed page number."""
        return self.download_log['last_page']
