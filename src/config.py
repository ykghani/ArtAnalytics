from pathlib import Path
from pydantic import EmailStr
from pydantic_settings import BaseSettings  # Changed this import
from typing import Optional

class Settings(BaseSettings):
    """Application settings with environment variable support."""
    
    # API Configuration
    API_BASE_URL: str = "https://api.artic.edu/api/v1/artworks"
    API_SEARCH_URL: str = "https://api.artic.edu/api/v1/artworks/search"
    USER_AGENT: str = "AIC-ArtDownloadBot/1.0"
    CONTACT_EMAIL: str = "yusuf.k.ghani@gmail.com"
    
    # File System Configuration
    PROJECT_ROOT: Optional[Path] = None
    AIC_DIR: Optional[Path] = None
    CACHE_DIR: Optional[Path] = None
    IMAGES_DIR: Optional[Path] = None
    LOGS_DIR: Optional[Path] = None
    CACHE_FILE: Optional[Path] = None
    PROGRESS_FILE: Optional[Path] = None
    
    # Download Configuration
    BATCH_SIZE: int = 100
    RATE_LIMIT_DELAY: float = 1.0  # seconds
    ERROR_RETRY_DELAY: float = 5.0  # seconds
    MAX_RETRIES: int = 5
    
    def initialize_paths(self, project_root: Path) -> None:
        """Initialize path configurations based on project root."""
        self.PROJECT_ROOT = project_root
        
        # Set up main AIC directory
        self.AIC_DIR = project_root / 'data' / 'aic'
        
        # Set up subdirectories
        self.CACHE_DIR = self.AIC_DIR / 'cache'
        self.IMAGES_DIR = self.AIC_DIR / 'images'
        self.LOGS_DIR = self.AIC_DIR / 'logs'
        
        # Set up files
        self.CACHE_FILE = self.AIC_DIR / 'aic_cache'  # sqlite extension added by requests_cache
        self.PROGRESS_FILE = self.AIC_DIR / 'processed_ids.json'
        
        # Ensure all directories exist
        self._ensure_directories()
    
    def _ensure_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        directories = [
            self.AIC_DIR,
            self.CACHE_DIR,
            self.IMAGES_DIR,
            self.LOGS_DIR
        ]
        for directory in directories:
            if directory:  # Check if not None before creating
                directory.mkdir(parents=True, exist_ok=True)
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

# Create global settings instance
settings = Settings()