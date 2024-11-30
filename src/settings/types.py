# src/settings/types.py
from enum import Enum
from pydantic import EmailStr, Field
from pydantic_settings import BaseSettings
from typing import Optional
from dataclasses import dataclass

class LogLevel(str, Enum): 
    '''Enum for logging levels'''
    NONE = "none"
    VERBOSE = "verbose"
    ERRORS_ONLY = "errors_only"
    
@dataclass
class MuseumInfo:
    """Basic information about a museum API"""
    name: str
    base_url: str
    code: str 
    user_agent: Optional[str] = None
    api_version: str = "v1"
    rate_limit: float = 1.0  # requests per second
    requires_api_key: bool = False

class MuseumConfig(BaseSettings):
    '''Configurations for specific museums'''
    api_base_url: str = Field(...)
    api_version: str = Field(default="v1")
    rate_limit: float = Field(default=1.0)
    user_agent: str = Field(...)
    contact_email: EmailStr = Field(default= "")
    api_key: Optional[str] = Field(default=None)
    code: str = Field(...)

    def to_museum_info(self) -> MuseumInfo:
        """Convert config to MuseumInfo instance"""
        return MuseumInfo(
            name=self.name if hasattr(self, 'name') else f"{self.code.upper()} Museum",
            base_url=self.api_base_url,
            code=self.code,
            user_agent=self.user_agent,
            api_version=self.api_version,
            rate_limit=self.rate_limit,
            requires_api_key=self.api_key is not None
        )

    class Config:
        validate_assignment = True
        extra = "allow"