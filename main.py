import sys
import logging
from pathlib import Path 
from typing import Dict, Any

from src.config import settings, LogLevel
from src.download import ArtworkDownloader, ProgressTracker
from src.museums.aic import AICClient, AICImageProcessor
from src.museums.schemas import MuseumInfo, ArtworkMetadata

def setup_logging(log_dir: Path, log_level: LogLevel) -> None: 
    '''Configure logging for application based on specific log level'''
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    
    handlers = []
    if log_level != LogLevel.NONE:
        handlers = [
            logging.FileHandler(log_dir / 'artwork_downloader.log'),
            logging.StreamHandler()
        ]
    
    for handler in handlers:
        handler.setFormatter(logging.Formatter(log_format))
    
    root_logger = logging.getLogger()
    root_logger.handlers = [] #clear existing handlers
    
    if log_level == LogLevel.NONE:
        root_logger.setLevel(logging.CRITICAL + 1)
    elif log_level == LogLevel.ERRORS_ONLY:
        root_logger.setLevel(logging.ERROR)
    else:
        root_logger.setLevel(logging.INFO)
    
    for handler in handlers:
        root_logger.addHandler(handler)
        
    if log_level != LogLevel.NONE:
        logging.info(f"Logging configured with level: {log_level}")

def get_museum_config(museum_id: str) -> Dict[str, Any]:
    '''Get museum specific configuration and params'''
    if museum_id not in settings.museums:
        raise ValueError(f"Unknown museum ID: {museum_id}")
        
    museum_config = settings.museums[museum_id]
    museum_info = MuseumInfo(
        name="Art Institute of Chicago",
        base_url="https://api.artic.edu/api/v1",
        user_agent=museum_config.user_agent,
        rate_limit=museum_config.rate_limit,
        api_version=museum_config.api_version,
        requires_api_key=False  # AIC doesn't require an API key
    )
        
    configs = {
        'aic': {
            'client_class': AICClient,
            'processor_class': AICImageProcessor,
            'museum_info': museum_info,
            'params': {
                'is_public_domain': True, 
                'department_title': 'Prints and Drawings',
                'fields': 'id,title,artist_display,image_id,department_title,date_display,medium,dimensions,credit_line'
            }
        }
    }
    
    if museum_id not in configs:
        raise ValueError(f"No configuration available for museum: {museum_id}")
    
    return configs[museum_id]

def download_museum_collection(museum_id: str) -> None:
    '''Downloads art collection from a specific museum'''
    museum_config = get_museum_config(museum_id)
    museum_paths = settings.get_museum_paths(museum_id)
    
    #Setup cache and progress tracking paths
    cache_dir = museum_paths['cache']
    cache_file = cache_dir / 'aic_cache.sqlite'
    progress_file = cache_dir / 'processed_ids.json'
    
    cache_dir.mkdir(parents= True, exist_ok= True)
    
    # Initialize components with museum-specific settings
    client = museum_config['client_class'](
        museum_info = museum_config['museum_info'],
        api_key = settings.museums[museum_id].api_key,
        cache_file = cache_file
    )
    
    image_processor = museum_config['processor_class'](
        output_dir=museum_paths['images'],
        museum_info= museum_config['museum_info']
    )
    
    progress_tracker = ProgressTracker(progress_file)
    
    downloader = ArtworkDownloader(
        client=client,
        image_processor=image_processor,
        progress_tracker=progress_tracker,
        max_downloads=settings.max_downloads,
        max_storage_gb=settings.max_storage_gb
    )
    
    logging.info(f"Starting download for {museum_id} museum")
    downloader.download_collection(museum_config['params'])

def main():
    # Initialize settings
    project_root = Path(__file__).parent
    settings.initialize_paths(project_root)
    
    # Setup logging with configured level
    setup_logging(settings.logs_dir, settings.log_level)

    # Parse command line args
    if len(sys.argv) != 2:
        print("Usage: python main.py <museum_id>")
        print(f"Available museums: {', '.join(settings.museums.keys())}")
        sys.exit(1)
    
    museum_id = sys.argv[1].lower()
    
    try:
        download_museum_collection(museum_id)
    except KeyboardInterrupt:
        logging.info("Download process interrupted by user")
        sys.exit(0)
    except Exception as e: 
        logging.error(f'Error in downloading process: {e}')
        sys.exit(1)

if __name__ == "__main__":
    main()