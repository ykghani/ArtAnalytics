import requests
import json
import time
from io import BytesIO
from PIL import Image
import requests_cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pathlib import Path
import logging
from typing import Dict, Set, Union, List, Optional
import re

from .utils import (
    get_project_root,
    setup_logging,
    ensure_directory,
    sanitize_filename
)

from .config import settings

class AICDownloader:
    '''
    Downloads artwork from the Art Institute of Chicago API.
    
    Attributes:
        OUTPUT_FOLDER (Path): Directory where downloaded images are saved
        PROGRESS_FILE (Path): JSON file tracking download progress
        SEARCH_URL (str): AIC search API endpoint
        BASE_URL (str): AIC base API endpoint
        USER_AGENT (str): User agent string for API requests
        download_log (Dict[str, Union[List[int], Set[int], Dict[str, Dict[str, str]]]]): 
            Tracks download progress and errors
    '''
    def __init__(self):
        # Get project root and setup paths
        project_root = get_project_root()
        
        # Setup directories
        self.OUTPUT_FOLDER = settings.IMAGES_DIR
        self.PROGRESS_FILE = settings.PROGRESS_FILE
        
        # # Ensure directories exist
        # ensure_directory(self.OUTPUT_FOLDER)
        # ensure_directory(self.PROGRESS_FILE.parent)
        
        # Setup API endpoints
        self.SEARCH_URL = settings.API_SEARCH_URL
        self.BASE_URL = settings.API_BASE_URL
        self.USER_AGENT = f"{settings.USER_AGENT} ({settings.CONTACT_EMAIL})"
        
        # Setup logging
        setup_logging()
        
        self.headers = {
            "AIC-User-Agent": self.USER_AGENT
        }
        
        # Initialize download log
        self.download_log = {
            "success": [],
            "failed": [],
            "network_error": [],
            "image_processing_error": [],
            "other_error": {},
            "all": set(),
            "last_page": 0
        }
        
        # Setup session with retry strategy
        self.session = self._create_session()
        
        # Install cache
        requests_cache.install_cache(str(settings.CACHE_FILE), backend='sqlite')
        
        # Load existing progress
        self._load_progress()

    def _create_session(self) -> requests.Session:
        """Create and configure a requests session with retry strategy."""
        session = requests.Session()
        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _load_progress(self) -> None:
        """Load previously processed IDs from progress file."""
        if self.PROGRESS_FILE.exists():
            try:
                with open(self.PROGRESS_FILE, 'r') as f:
                    self.download_log = json.load(f)
                    # Convert 'all' back to set from list
                    self.download_log['all'] = set(self.download_log['all'])
                    
                    if 'last_page' not in self.download_log:
                        self.download_log['last_page'] = 0
                    
                logging.info(f"Loaded progress file. {len(self.download_log['all'])} items previously processed. Resuming from page {self.download_log['last_page'] + 1}")
            except json.JSONDecodeError:
                logging.error("Error reading progress file. Starting fresh.")
                self._save_progress()  # Create new progress file
        else:
            logging.info("No progress file found. Starting fresh.")
            self._save_progress()  # Create new progress file

    def _save_progress(self) -> None:
        """Save progress to file."""
        try:
            # Convert set to list for JSON serialization
            download_log_copy = self.download_log.copy()
            download_log_copy['all'] = list(download_log_copy['all'])
            
            with open(self.PROGRESS_FILE, 'w') as f:
                json.dump(download_log_copy, f, indent=4)
        except Exception as e:
            logging.error(f"Error saving progress: {str(e)}")

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

    def download_image(self, aic_id: int, img_id: str, title: str, artist: str) -> None:
        """Download and save a single image."""
        if not img_id:
            logging.warning(f"No image ID for AIC ID {aic_id}")
            self.log_status(aic_id, "failed", "No image ID available")
            return

        iiif_url = f"https://www.artic.edu/iiif/2/{img_id}/full/843,/0/default.jpg"
        
        try:
            img_response = self.session.get(iiif_url, headers=self.headers)
            img_response.raise_for_status()
            
            image = Image.open(BytesIO(img_response.content))
            
            filename = sanitize_filename(aic_id= aic_id, title= title, artist= artist, max_length= 255)
            filepath = self.OUTPUT_FOLDER / filename
            
            image.save(filepath)
            logging.info(f"Successfully downloaded AIC ID {aic_id}")
            self.log_status(aic_id, "success")
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error downloading AIC ID {aic_id}: {str(e)}")
            self.log_status(aic_id, "network_error", str(e))
        except Exception as e:
            logging.error(f"Error processing image for AIC ID {aic_id}: {str(e)}")
            self.log_status(aic_id, "image_processing_error", str(e))

    def download_all_artwork(self, force_restart: bool = False) -> None:
        """
        Download all public domain artwork from the Prints and Drawings department.
        
        Args: 
            force_restart(bool): If true, starts from page 1 regardless of previous progress
        
        """
        fields = ['id', 'title', 'artist_display', 'image_id', 'department_title']
        params = {
            'is_public_domain': 'true',
            'department_title': 'Prints and Drawings',
            'fields': ','.join(fields),
            'limit': 100
        }
        
        total_processed = len(self.download_log['all'])
        logging.info(f"Starting download. {total_processed} items already processed.")
        
        page = 1 if force_restart else self.download_log['last_page'] + 1
        while True:
            params['page'] = page
            
            try:
                response = self.session.get(self.BASE_URL, params=params, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                
                if not data.get('data'):
                    logging.info("No more artwork to process")
                    break
                
                logging.info(f"Processing page {page}")
                
                for art in data['data']:
                    aic_id = art['id']
                    
                    if aic_id in self.download_log['all']:
                        logging.debug(f"ID {aic_id} already processed. Skipping!")
                        continue
                    
                    if art['department_title'] != 'Prints and Drawings':
                        self.log_status(aic_id, "skipped", "Not in Prints and Drawings department")
                        continue
                    
                    self.download_image(
                        aic_id,
                        art.get('image_id'),
                        art.get('title', 'Untitled'),
                        art.get('artist_display', 'Unknown Artist')
                    )
                    time.sleep(1)  # Rate limiting
                
                self.download_log['last_page'] = page
                self._save_progress()
                
                page += 1
                time.sleep(1)  # Rate limiting between pages
                
            except requests.exceptions.RequestException as e:
                logging.error(f"Error fetching page {page}: {str(e)}")
                time.sleep(5)  # Wait longer on error
                continue
            except Exception as e:
                logging.error(f"Unexpected error on page {page}: {str(e)}")
                break

        logging.info("Download process completed!")
        new_total = len(self.download_log['all'])
        logging.info(f"Processed {new_total - total_processed} new items. Total items: {new_total}")

    def retry_failed_downloads(self):
        """Retry failed downloads with appropriate strategies."""
        failures = self.analyze_failures()
        
        # 1. Retry network errors
        if failures['network_error']:
            logging.info(f"Retrying {len(failures['network_error'])} network errors...")
            for aic_id in failures['network_error']:
                # Fetch artwork details again
                response = self.session.get(
                    f"{self.BASE_URL}/{aic_id}",
                    params={'fields': 'id,title,artist_display,image_id'},
                    headers=self.headers
                )
                if response.ok:
                    art = response.json()['data']
                    self.download_image(
                        aic_id,
                        art.get('image_id'),
                        art.get('title', 'Untitled'),
                        art.get('artist_display', 'Unknown Artist')
                    )
                time.sleep(2)  # More conservative rate limiting for retries
        
        # 2. Retry filename_too_long with more aggressive truncation
        if failures['filename_too_long']:
            logging.info(f"Retrying {len(failures['filename_too_long'])} failed due to filename length...")
            for aic_id in failures['filename_too_long']:
                response = self.session.get(
                    f"{self.BASE_URL}/{aic_id}",
                    params={'fields': 'id,title,artist_display,image_id'},
                    headers=self.headers
                )
                if response.ok:
                    art = response.json()['data']
                    # Use more aggressive filename truncation
                    title = art.get('title', 'Untitled')[:30]  # More aggressive truncation
                    artist = art.get('artist_display', 'Unknown Artist')[:20]
                    self.download_image(aic_id, art.get('image_id'), title, artist)
                time.sleep(2)
        
        # 3. Generate report of unrecoverable failures
        unrecovarable = {
            'no_image_id': failures['no_image_id'],
            'other_errors': failures['other_errors']
        }
        
        # Save report
        report_path = self.OUTPUT_FOLDER.parent / 'failed_downloads_report.json'
        with open(report_path, 'w') as f:
            json.dump(unrecovarable, f, indent=2)

    def generate_failure_report(self) -> str:
        """Generate a detailed report of all failures."""
        failures = self.analyze_failures()
        
        report = ["Download Failure Report", "===================\n"]
        
        # Summary
        total_failed = sum(len(ids) for ids in failures.values())
        report.append(f"Total Failed Downloads: {total_failed}\n")
        
        # Breakdown by category
        for category, ids in failures.items():
            report.append(f"\n{category.replace('_', ' ').title()}: {len(ids)}")
            report.append("-" * 40)
            for aic_id in ids:
                error_msg = self.download_log['other_error'].get(str(aic_id), 'No specific error message')
                report.append(f"AIC ID {aic_id}: {error_msg}")
        
        # Save report
        report_path = self.OUTPUT_FOLDER.parent / 'failure_report.txt'
        with open(report_path, 'w') as f:
            f.write('\n'.join(report))
        
        return report_path

def main():
    downloader = AICDownloader()
    downloader.download_all_artwork()

if __name__ == "__main__":
    main()