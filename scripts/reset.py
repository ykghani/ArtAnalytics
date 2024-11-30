import argparse
import shutil
from pathlib import Path
import logging
from typing import Optional
import sqlite3

from src.config import settings
from src.database.database import Database
from src.database.models import Base

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

def reset_database(museum_code: Optional[str] = None):
    """Reset database entirely or just records for a specific museum"""
    db = Database(settings.database_path)
    
    if museum_code:
        # Delete records for specific museum
        with db.get_session() as session:
            museum = session.query(db.Museum).filter_by(code=museum_code).first()
            if museum:
                logging.info(f"Deleting all artwork records for {museum.name}")
                session.query(db.Artwork).filter_by(museum_id=museum.id).delete()
                session.commit()
    else:
        # Drop all tables and recreate schema
        logging.info("Dropping all database tables")
        Base.metadata.drop_all(db.engine)
        logging.info("Recreating database schema")
        Base.metadata.create_all(db.engine)

def clean_museum_files(museum_code: Optional[str] = None):
    """Delete image and cache files for all museums or a specific one"""
    if museum_code:
        museum_paths = settings.get_museum_paths(museum_code)
        museums_to_clean = {museum_code: museum_paths}
    else:
        museums_to_clean = {
            code: settings.get_museum_paths(code) 
            for code in settings.museums.keys()
        }

    for code, paths in museums_to_clean.items():
        # Clean images
        if paths['images'].exists():
            logging.info(f"Deleting images for {code}")
            shutil.rmtree(paths['images'])
            paths['images'].mkdir(parents=True)
            
        # Clean cache
        if paths['cache'].exists():
            logging.info(f"Deleting cache for {code}")
            shutil.rmtree(paths['cache'])
            paths['cache'].mkdir(parents=True)

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

    setup_logging()
    
    # Initialize paths
    project_root = Path(__file__).parent
    settings.initialize_paths(project_root)

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
        reset_database(args.museum)
        clean_museum_files(args.museum)
        logging.info(f"Successfully reset data for {target}")
    except Exception as e:
        logging.error(f"Error during reset: {e}")
        raise

if __name__ == "__main__":
    main()