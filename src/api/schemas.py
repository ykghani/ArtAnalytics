from enum import Enum
from pydantic import BaseModel
from typing import Dict, Any, Optional
from datetime import datetime

class ClientType(Enum):
    WEB = "web"
    MACOS = "macos"
    TV = "tv"
    CHROME = "chrome"

class ImageMetadata(BaseModel):
    url: str
    width: Optional[int]
    height: Optional[int]
    format: str

class ArtworkResponse(BaseModel):
    id: str
    title: str
    artist: str
    date_created: Optional[str]
    medium: Optional[str]
    dimensions: Optional[str]
    credit_line: Optional[str]
    department: Optional[str]
    museum: str
    image_urls: Dict[str, ImageMetadata]
    last_updated: datetime
    description: Optional[str]