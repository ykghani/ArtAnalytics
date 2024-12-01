from dataclasses import dataclass
from typing import Optional

@dataclass
class MuseumInfo:
    """Basic information about a museum API"""
    name: str
    base_url: str
    code: str 
    user_agent: Optional[str] = None
    api_version: str = "v1"
    rate_limit: float = 1.0
    requires_api_key: bool = False