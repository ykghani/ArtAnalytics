import sys
import logging
from pathlib import Path 
from typing import Dict, Any, List 
import concurrent.futures

from src.config import settings
from src.download import ArtworkDownloader, BaseProgressTracker, ImageProcessor
from src.log_level import log_level
from src.museums.aic import AICClient, AICImageProcessor, AICProgressTracker
from src.museums.met import MetClient, MetImageProcessor, MetProgressTracker
from src.museums.cma import CMAClient, CMAImageProcessor, CMAProgressTracker
from src.museums.schemas import MuseumInfo, ArtworkMetadata
from src.utils import setup_logging

def download_museum_collection_wrapper(args: tuple) -> None:
    '''Wrapper function to unpack arguments for concurrent execution'''
    museum_id, settings = args
    try:
        download_museum_collection(museum_id= museum_id)
    except Exception as e:
        logging.error(f"Error downloading from {museum_id}: {e}")
        raise

def run_parallel_downloads(museum_ids: List[str], max_workers: int = 3) -> None: 
    '''Run multiple museum downloaders in parallel
    
    Args:
        museum_ids: List of museum IDs to process
        max_workers: Max number of concurrent downloads
    '''
    
    #Create args for each download task
    download_args = [(museum_id, settings) for museum_id in museum_ids]
    
    with concurrent.futures.ThreadPoolExecutor(max_workers= max_workers) as executor:
        future_to_museum = {
            executor.submit(download_museum_collection_wrapper, args): args[0]
            for args in download_args
        }
        
        for future in concurrent.futures.as_completed(future_to_museum):
            museum_id = future_to_museum[future]
            try:
                future.result()
                logging.info(f"Successfully completed download for {museum_id}")
            except Exception as e: 
                logging.error(f"Download failed for {museum_id}: {e}")


def create_museum_info(museum_id: str, config: Dict[str, Any]) -> MuseumInfo: 
    """Create MuseumInfo instance based on museum configuration"""
    museum_names = {
        'aic': "Art Institute of Chicago",
        'met': "Metropolitan Museum of Art",
        'cma': "Cleveland Museum of Art"
    }
    
    return MuseumInfo(
        name=museum_names.get(museum_id, "Unknown Museum"),
        base_url=config.api_base_url,
        code= museum_id,
        user_agent=config.user_agent,
        rate_limit=config.rate_limit,
        api_version=config.api_version,
        requires_api_key=False  # Neither museum requires API key
    )

def get_museum_config(museum_id: str) -> Dict[str, Any]:
    '''Get museum specific configuration and params'''
    if museum_id not in settings.museums:
        raise ValueError(f"Unknown museum ID: {museum_id}")
        
    # museum_info = settings.get_museum_info(museum_id)
    museum_config = settings.museums[museum_id]
    museum_info = create_museum_info(museum_id, museum_config)
    museum_paths = settings.get_museum_paths(museum_id)
    
    query_params = {
    'aic': settings.museum_queries.get_aic_params(),
    'met': settings.museum_queries.get_met_params(),
    'cma': settings.museum_queries.get_cma_params()
            }.get(museum_id, {})
        
    configs = {
        'aic': {
            'client_class': AICClient,
            'processor_class': AICImageProcessor,
            'tracker_class': AICProgressTracker,
            'museum_info': museum_info,
            'params': query_params
        },
        'met': {
            'client_class': MetClient,
            'processor_class': MetImageProcessor,
            'tracker_class': MetProgressTracker, 
            'museum_info': museum_info,
            'params': query_params
        },
        'cma': {
            'client_class': CMAClient,
            'processor_class': CMAImageProcessor,
            'tracker_class': CMAProgressTracker,
            'museum_info': museum_info,
            'params': query_params
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
    cache_file = cache_dir / f"{museum_id}_cache.sqlite"
    progress_file = cache_dir / 'processed_ids.json'
    cache_dir.mkdir(parents= True, exist_ok= True)
    
    #Initialize progress tracker
    progress_tracker = museum_config['tracker_class'](
        progress_file= progress_file)
    
    # Initialize components with museum-specific settings
    client = museum_config['client_class'](
        museum_info = museum_config['museum_info'],
        api_key = settings.museums[museum_id].api_key,
        cache_file = cache_file,
        progress_tracker = progress_tracker
    )
    
    image_processor = museum_config['processor_class'](
        output_dir=museum_paths['images'],
        museum_info= museum_config['museum_info']
    )
    
    downloader = ArtworkDownloader(
        client=client,
        image_processor=image_processor,
        progress_tracker=progress_tracker,
        settings= settings
    )
    
    logging.info(f"Starting download for {museum_id} museum")
    logging.info(f"Museum params: {museum_config['params']}")
    downloader.download_collection(museum_config['params'])

def main():
    # Initialize settings
    project_root = Path(__file__).parent
    settings.initialize_paths(project_root)
    
    # Setup logging with configured level
    setup_logging(settings.logs_dir, log_level, None)
    
    if len(sys.argv) > 1:
        museum_ids = sys.argv[1: ]
    else:
        museum_ids = list(settings.museums.keys())
    
    valid_museums = set(settings.museums.keys())
    invalid_museums = set(museum_ids) - valid_museums
    if invalid_museums:
        print(f"Invalid museum IDs: {', '.join(invalid_museums)}")
        print(f"Available museums: {', '.join(valid_museums)}")
        sys.exit(1)
    
    try:
        run_parallel_downloads(museum_ids)
    except KeyboardInterrupt:
        logging.info(f"Download process interrupted by user")
        sys.exit(0)
    except Exception as e: 
        logging.error(f'Error in download process: {e}')
        sys.exit(1)

if __name__ == "__main__":
    main()