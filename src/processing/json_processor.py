#json_processor.py 
'''
Module for processing folder of JSON data into a structured format for EDA
'''

import pandas as pd
import json
from pathlib import Path
import logging
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm

from ..settings.config import settings
from ..utils import setup_logging

class ArtworkJSONProcessor: 
    '''Processed artwork JSON files into structured data frame'''
    
    def __init__(self, json_dir: str = None):
        """
        Initialize the JSON processor.
        
        Args:
            json_dir: Path to JSON directory. If None, uses default from settings
        """
        self.json_dir = Path(json_dir) if json_dir else Path("../artic-api-data/json/artworks")
        self.logger = logging.getLogger(__name__)
    
    def _load_json_file(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """
        Load a single JSON file safely.
        
        Args:
            file_path: Path to the JSON file
            
        Returns:
            Dict containing the JSON data if successful, None otherwise
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.logger.error(f"Error reading {file_path}: {str(e)}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error reading {file_path}: {str(e)}")
            return None

    def process_artwork_jsons(self) -> pd.DataFrame:
        """
        Process all artwork JSON files into a pandas DataFrame.
        
        Returns:
            pandas DataFrame containing consolidated artwork data
        """
        if not self.json_dir.exists():
            raise FileNotFoundError(f"Directory not found: {self.json_dir}")
        
        self.logger.info(f"Starting artwork JSON processing from {self.json_dir}")
        
        # Get list of all JSON files
        json_files = list(self.json_dir.glob('*.json'))
        self.logger.info(f"Found {len(json_files)} JSON files to process")
        
        # Initialize list to store artwork data
        artwork_data: List[Dict[str, Any]] = []
        
        # Process files with progress bar
        with ThreadPoolExecutor() as executor:
            # Create iterator with progress bar
            futures = list(tqdm(
                executor.map(self._load_json_file, json_files),
                total=len(json_files),
                desc="Processing JSON files"
            ))
            
            # Filter out None values and extend artwork_data
            artwork_data.extend([data for data in futures if data is not None])
        
        # Convert to DataFrame
        df = pd.DataFrame(artwork_data)
        
        # Basic data cleaning
        # Remove any completely empty columns
        df = df.dropna(axis=1, how='all')
        
        self.logger.info(f"Successfully processed {len(df)} artworks into DataFrame")
        self.logger.info(f"DataFrame columns: {df.columns.tolist()}")
        
        return df

    def save_processed_data(self, df: pd.DataFrame, output_dir: Path = None) -> None:
        """
        Save processed DataFrame to disk in multiple formats.
        
        Args:
            df: Processed DataFrame to save
            output_dir: Directory to save files. If None, uses default from settings
        """
        if output_dir is None:
            output_dir = Path(settings.AIC_DIR) / 'processed'
        
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Save in multiple formats
        df.to_csv(output_dir / 'artwork_database.csv', index=False)
        df.to_parquet(output_dir / 'artwork_database.parquet')
        
        self.logger.info(f"Saved processed data to {output_dir}")


def main():
    """Main function for running the processor directly."""
    # Setup logging using existing utility
    setup_logging()
    
    # Create processor instance
    processor = ArtworkJSONProcessor()
    
    # Process JSONs
    df = processor.process_artwork_jsons()
    
    # Save results
    processor.save_processed_data(df)
    
    # Print basic info
    print("\nDataset Info:")
    print(f"Total records: {len(df)}")
    print("\nColumns:")
    for col in df.columns:
        non_null = df[col].count()
        print(f"{col}: {non_null} non-null values")


if __name__ == "__main__":
    main()