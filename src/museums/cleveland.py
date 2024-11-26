from typing import Dict, List, Any, Optional, Iterator
from pathlib import Path
from PIL import Image
from io import BytesIO
import logging

from requests.sessions import Session as Session

from .base import MuseumAPIClient, MuseumImageProcessor
from .schemas import ArtworkMetadata, MuseumInfo
from ..utils import sanitize_filename

class ClevelandClient(MuseumAPIClient): 
    pass

class ClevelandImageProcessor(MuseumAPIClient):
    pass 