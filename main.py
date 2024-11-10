import sys
import logging
from pathlib import Path 

# from src.download import AICDownloader
# from src.config import settings

# from src.config import settings
# from src.download.api_client import AICApiClient
# from src.download.progress_tracker import ProgressTracker
# from src.download.image_processor import ImageProcessor
# from src.download.artwork_downloader import ArtworkDownloader

from src.config import settings
from src.download import (
    AICApiClient, 
    ArtworkDownloader, 
    ImageProcessor, 
    ProgressTracker
)

def setup_logging(log_dir: Path) -> None: 
    '''Configure logging for application'''
    logging.basicConfig(
        level= logging.INFO,
        format= '%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_dir / 'aic_downloader.log'),
            logging.StreamHandler()
        ]
    )

def main():
    
    #Initialize settings
    project_root = Path(__file__).parent
    settings.initialize_paths(project_root)
    
    #Setup logging
    setup_logging(settings.LOGS_DIR)
    logging.info(f"Starting AIC artwork download process...")

    
    #Initialize components
    api_client = AICApiClient(
        base_url = settings.API_BASE_URL, 
        search_url = settings.API_SEARCH_URL, 
        user_agent = f"{settings.USER_AGENT} ({settings.CONTACT_EMAIL})",
        cache_file = str(settings.CACHE_FILE)
    )
    logging.info(f"API client initialized")
    
    progress_tracker = ProgressTracker(settings.PROGRESS_FILE)
    logging.info("Progress tracking initialized")
    
    image_processor = ImageProcessor(settings.IMAGES_DIR)
    logging.info("Image processing initialized")

    #Create downloader
    downloader = ArtworkDownloader(
        api_client = api_client, 
        progress_tracker = progress_tracker, 
        image_processor = image_processor, 
        rate_limit_delay = settings.RATE_LIMIT_DELAY
    )
    
    downloader.download_all_artwork()
    logging.info("Download process complete")

if __name__ == "__main__":
    main()