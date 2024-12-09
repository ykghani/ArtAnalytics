import json
from pathlib import Path
import logging
from typing import Iterator

from ..museums.schemas import ArtworkMetadata, CMAArtworkFactory
from ..museums.cma import CMAClient, CMAImageProcessor
from ..config import settings
from .progress_tracker import CMAProgressTracker

def process_cma_dump(data_file: Path) -> Iterator: 
    '''Process CMA data dump and yield artwork metadata'''
    artwork_factory = CMAArtworkFactory() 
    
    with open(data_file, 'r') as f: 
        data = json.load(f)
    