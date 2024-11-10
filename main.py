import sys
import logging
from pathlib import Path 

from src.config import settings
from src.config import LogLevel
from src.download import (
    AICApiClient, 
    ArtworkDownloader, 
    ImageProcessor, 
    ProgressTracker
)

def setup_logging(log_dir: Path, log_level: LogLevel) -> None: 
    '''Configure logging for application based on specific log level'''
    
    #Define handler format
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    
    #Create handlers
    file_handler = logging.FileHandler(log_dir / 'aic_downloader.log')
    console_handler = logging.StreamHandler()
    
    formatter = logging.Formatter(log_format)
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    #Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers = [] #clear existing handlers
    
    #Setup logging levels
    if log_level == LogLevel.NONE:
        root_logger.setLevel(logging.CRITICAL + 1)
    elif log_level == LogLevel.ERRORS_ONLY:
        root_logger.setLevel(logging.ERROR)
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
    else:
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        
    if log_level != LogLevel.NONE:
        logging.info(f"Logging configured with level: {log_level}")
    
    # logging.basicConfig(
    #     level= logging.INFO,
    #     format= '%(asctime)s - %(levelname)s - %(message)s',
    #     handlers=[
    #         logging.FileHandler(log_dir / 'aic_downloader.log'),
    #         logging.StreamHandler()
    #     ]
    # )

def main():
    
    #Initialize settings
    project_root = Path(__file__).parent
    settings.initialize_paths(project_root)
    
    #Setup logging with configured level
    setup_logging(settings.LOGS_DIR, settings.LOG_LEVEL)
    if settings.LOG_LEVEL != LogLevel.NONE: 
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