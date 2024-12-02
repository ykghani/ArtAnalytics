import argparse
import shutil
from pathlib import Path
import logging
from typing import Optional
import sqlite3

from src.config import settings
from src.database.database import Database
from src.database.models import Base, Museum, Artwork
from src.log_level import LogLevel, log_level
from src.utils import setup_logging


def reset_database(museum_code: Optional[str], logger) -> None:
    """Reset database entirely or just records for a specific museum"""
    db = Database(settings.database_path)
    
    try:
        if museum_code:
            with db.get_session() as session:
                # First ensure tables exist
                Base.metadata.create_all(db.engine)
                
                # Then initialize museums if needed
                db.init_museums(session)
                
                museum = session.query(Museum).filter_by(code=museum_code).first()
                if museum:
                    logger.progress(f"Deleting all artwork records for {museum.name}")
                    session.query(Artwork).filter_by(museum_id=museum.id).delete()
                    session.commit()
        else:
            # Full reset
            logger.progress("Dropping all database tables")
            Base.metadata.drop_all(db.engine)
            logger.progress("Recreating database schema")
            Base.metadata.create_all(db.engine)
            
            # Initialize museums after recreation
            with db.get_session() as session:
                db.init_museums(session)
    except Exception as e:
        logger.error(f"Database error: {str(e)}")
        raise

def clean_museum_files(museum_code: Optional[str], logger) -> None:
    """Delete image, cache, and log files for all museums or a specific one"""
    if museum_code:
        museum_paths = settings.get_museum_paths(museum_code)
        logger.progress(f"Using paths: {museum_paths}")
        museums_to_clean = {museum_code: museum_paths}
    else:
        museums_to_clean = {
            code: settings.get_museum_paths(code) 
            for code in settings.museums.keys()
        }

    for code, paths in museums_to_clean.items():
        # Clean images
        if paths['images'].exists():
            logger.progress(f"Deleting images for {code}")
            shutil.rmtree(paths['images'])
            paths['images'].mkdir(parents=True)
            
        # Clean cache
        if paths['cache'].exists():
            logger.progress(f"Deleting cache for {code}")
            shutil.rmtree(paths['cache'])
            paths['cache'].mkdir(parents=True)
            
        # Clean logs
        log_file = settings.logs_dir / f"{code}_downloader.log"
        if log_file.exists():
            logger.progress(f"Deleting log file for {code}")
            log_file.unlink()  

def verify_cleanup(museum_code: Optional[str], logger) -> None:
    """Verify that cleanup was successful"""
    db = Database(settings.database_path)
    
    with db.get_session() as session:
        if museum_code:
            museum = session.query(Museum).filter_by(code=museum_code).first()
            if museum:
                artwork_count = session.query(Artwork).filter_by(museum_id=museum.id).count()
                logger.progress(f"Artwork records remaining for {museum.name}: {artwork_count}")
        else:
            artwork_count = session.query(Artwork).count()
            logger.progress(f"Total artwork records remaining: {artwork_count}")

        # Verify museum exists (should still exist even after cleanup)
        museums = session.query(Museum).all()
        logger.progress(f"Museums in database: {[m.code for m in museums]}")
        
def verify_directories(museum_code: Optional[str], logger) -> None:
    """Verify directory cleanup"""
    if museum_code:
        paths = settings.get_museum_paths(museum_code)
        museums_to_check = {museum_code: paths}
    else:
        museums_to_check = {
            code: settings.get_museum_paths(code) 
            for code in settings.museums.keys()
        }

    for code, paths in museums_to_check.items():
        # Check images directory
        image_path = paths['images']
        if image_path.exists():
            image_count = len(list(image_path.glob('*.jpg')))
            logger.progress(f"Images remaining in {code} directory: {image_count}")
            if image_count > 0:
                logger.error(f"Found {image_count} images in {image_path}, deletion may have failed")

        # Check cache directory
        cache_path = paths['cache']
        if cache_path.exists():
            cache_files = list(cache_path.glob('*'))
            if cache_files:
                logger.error(f"Found files in cache directory: {[f.name for f in cache_files]}")

def main():
    parser = argparse.ArgumentParser(description='Reset artwork database and storage')
    parser.add_argument(
        '--museum', 
        choices=['aic', 'met', 'cma'],
        help='Specific museum to reset. If not provided, resets everything'
    )
    parser.add_argument(
        '--force', 
        action='store_true',
        help='Skip confirmation prompt'
    )
    args = parser.parse_args()
    
    # Initialize paths
    project_root = Path(__file__).parent.parent
    settings.initialize_paths(project_root)
    
    logger = setup_logging(settings.logs_dir, log_level)

    # Confirmation prompt
    target = args.museum if args.museum else "ALL museums"
    if not args.force:
        confirm = input(
            f"This will delete all data for {target}. "
            f"This cannot be undone. Continue? [y/N]: "
        )
        if confirm.lower() != 'y':
            logging.info("Operation cancelled")
            return

    try:
        reset_database(args.museum, logger= logger)
        clean_museum_files(args.museum, logger= logger)
        logger.progress(f"Successfully reset data for {target}")
        
        logger.progress("\nVerifying cleanup:")
        verify_cleanup(args.museum, logger)
        verify_directories(args.museum, logger)
        
    except Exception as e:
        logger.error(f"Error during reset: {e}")
        raise

if __name__ == "__main__":
    main()