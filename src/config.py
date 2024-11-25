from pathlib import Path
from pydantic import EmailStr, Field
from pydantic_settings import BaseSettings  
from typing import Optional, Dict
from enum import Enum

class LogLevel(str, Enum): 
    '''Enum for logging levels'''
    NONE = "none"
    VERBOSE = "verbose"
    ERRORS_ONLY = "errors_only"

class MuseumConfig(BaseSettings):
    '''Configurations for specific museums'''
    api_base_url: str = Field(...)
    api_version: str = Field(default="v1")
    rate_limit: float = Field(default=1.0)
    user_agent: str = Field(...)
    contact_email: EmailStr = Field(...)
    api_key: Optional[str] = Field(default=None)

    class Config:
        validate_assignment = True
        extra = "allow"

class Settings(BaseSettings):
    """Application settings with environment variable support."""
    
    # Initialize museums configs
    museums: Dict[str, MuseumConfig] = Field(
        default_factory= lambda: {
            'aic': MuseumConfig(
                api_base_url= "https://api.artic.edu/api/v1/artworks",
                user_agent= "AIC-ArtDownloadBot/1.0",
                contact_email= "yusuf.k.ghani@gmail.com"
            ),
            'met': MuseumConfig(
                api_base_url = "https://collectionapi.metmuseum.org/public/collection/v1",
                rate_limit = 80.0
            )
        }
    )
    
    #Logging configuration
    log_level: LogLevel = Field(default=LogLevel.VERBOSE)
    
    # File System Configuration
    project_root: Optional[Path] = Field(default=None)
    data_dir: Optional[Path] = Field(default=None)
    cache_dir: Optional[Path] = Field(default=None)
    images_dir: Optional[Path] = Field(default=None)
    logs_dir: Optional[Path] = Field(default=None)
    cache_file: Optional[Path] = Field(default=None)
    
    #Museum-specific directories
    museum_dirs: Dict[str, Path] = Field(default_factory=dict)
    
    # Download Configuration
    batch_size: int = Field(default=100)
    rate_limit_delay: float = Field(default=1.0)
    error_retry_delay: float = Field(default=5.0)
    max_retries: int = Field(default=5)
    max_downloads: Optional[int] = Field(default=None)
    max_storage_gb: Optional[float] = Field(default=None)
    
    def initialize_paths(self, project_root: Path) -> None:
        """Initialize path configurations based on project root."""
        self.project_root = project_root
        self.data_dir = project_root / 'data'
        
        #Setup shared directories
        self.cache_dir = self.data_dir / 'cache'
        self.logs_dir = self.data_dir / 'logs'
        
        #Museum specific directories
        self.museum_dirs = {
            'aic': self.data_dir / 'aic',
            'met': self.data_dir / 'met'
        }
        
        for museum, base_dir in self.museum_dirs.items():
            (base_dir / 'images').mkdir(parents=True, exist_ok=True)
            (base_dir / 'cache').mkdir(parents=True, exist_ok=True)
        
        # Ensure all directories exist
        self._ensure_directories()
    
    def get_museum_paths(self, museum_id: str) -> Dict[str, Path]: 
        '''Get paths for a specific museum'''
        if museum_id not in self.museum_dirs: 
            raise ValueError(f"Unknown museum ID: {museum_id}")
        
        base_dir = self.museum_dirs[museum_id]
        museum_cache = base_dir / 'cache'
        
        return {
            'base': base_dir,
            'images': base_dir / 'images',
            'cache': museum_cache,
            'processed_ids': museum_cache / 'processed_ids.json'
        }
    
    def _ensure_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        directories = [
            self.data_dir,
            self.cache_dir, 
            self.logs_dir,
            *self.museum_dirs.values()
        ]
        
        for directory in directories:
            if directory:  # Check if not None before creating
                directory.mkdir(parents=True, exist_ok=True)
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = 'allow'

# Create global settings instance
settings = Settings()
log_level = LogLevel("verbose")