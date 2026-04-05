import os
from pathlib import Path
from pydantic import EmailStr, Field
from pydantic_settings import BaseSettings
from typing import Optional, Dict, Any
from enum import Enum

from .museums.museum_info import MuseumInfo
from .log_level import LogLevel
from artserve_shared.museums import MUSEUMS as SHARED_MUSEUMS


class MuseumQuerySettings(BaseSettings):
    """Museum-specific API query parameters"""

    # Met Museum parameters
    met_departments: str = Field(
        default="1|5|6|9|10|11|12|13|14|15|17|19|21",
        # 1: American Decorative Arts, 5: Africa/Oceania/Americas, 6: Asian Art,
        # 9: Drawings & Prints, 10: Egyptian Art, 11: European Paintings,
        # 12: European Sculpture/Decorative Arts, 13: Greek & Roman Art, 14: Islamic Art,
        # 15: Robert Lehman Collection, 17: Medieval Art, 19: Photographs, 21: Modern Art
        description="Pipe-separated department IDs for Met Museum",
    )

    # AIC parameters
    aic_departments: str = Field(
        default="Modern Art|Contemporary Art|Prints and Drawings|Photography",
        description="Pipe-separated department names for AIC",
    )
    aic_artwork_types: str = Field(
        default="Painting|Drawing|Print|Photograph",
        description="Pipe-separated artwork types for AIC",
    )

    # CMA parameters
    cma_departments: str = Field(
        default="|".join(
            [
                "African Art",
                "American Painting and Sculpture",
                "Art of the Americas",
                "Chinese Art",
                "Contemporary Art",
                "Decorative Art and Design",
                "Drawings",
                "Egyptian and Ancient Near Eastern Art",
                "European Painting and Sculpture",
                "Greek and Roman Art",
                "Indian and South East Asian Art",
                "Islamic Art",
                "Japanese Art",
                "Korean Art",
                "Medieval Art",
                "Modern European Painting and Sculpture",
                "Oceania",
                "Photography",
                "Prints",
                "Textiles",
            ]
        ),
        description="Pipe-separated department names for CMA, excludes depts that don't translate well to digital use cases (e.g., Performing Arts, Music & Film)",
    )
    cma_types: str = Field(
        default="Drawing|Painting|Photograph|Print",
        description="Pipe-separated artwork types for CMA",
    )

    def get_met_params(self) -> Dict[str, Any]:
        """Get Met Museum query parameters"""
        return {
            "departmentIds": self.met_departments,
            # Note: Omitting isHighlight parameter to get ALL artworks (highlights + non-highlights)
            "hasImages": True,
            "isPublicDomain": True,
        }

    def get_aic_params(self) -> Dict[str, Any]:
        """Get AIC query parameters"""
        return {
            "department_title": self.aic_departments,
            "artwork_type_title": self.aic_artwork_types,
            "is_public_domain": True,
            "has_multimedia_resources": True,
        }

    def get_cma_params(self) -> Dict[str, Any]:
        """Get CMA query parameters"""
        return {
            # 'department': self.cma_departments,
            # 'type': self.cma_types,
            "has_image": 1,
            "cc0": None,
        }


class MuseumConfig(BaseSettings):
    """Configurations for specific museums"""

    api_base_url: str = Field(...)
    api_version: str = Field(default="v1")
    rate_limit: float = Field(default=1.0)
    user_agent: str = Field(...)
    contact_email: EmailStr = Field(default="")
    api_key: Optional[str] = Field(default=None)
    code: str = Field(...)
    use_data_dump: bool = Field(default=False)
    data_dump_path: Optional[Path] = Field(default=None)

    def to_museum_info(self) -> MuseumInfo:
        """Convert config to MuseumInfo instance"""
        return MuseumInfo(
            name=self.name if hasattr(self, "name") else f"{self.code.upper()} Museum",
            base_url=self.api_base_url,
            code=self.code,
            user_agent=self.user_agent,
            api_version=self.api_version,
            rate_limit=self.rate_limit,
            requires_api_key=self.api_key is not None,
        )

    class Config:
        validate_assignment = True
        extra = "allow"


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    log_level: LogLevel = Field(
        default=LogLevel.PROGRESS,
        env="LOG_LEVEL",
        description="Logging level for the application",
    )

    # Common Settings
    default_contact_email: EmailStr = Field(..., env="DEFAULT_CONTACT_EMAIL")

    # Museum-specific Settings
    aic_api_base_url: str = Field(
        default="https://api.artic.edu/api/v1/artworks", env="AIC_API_BASE_URL"
    )
    aic_user_agent: str = Field(default="AIC-ArtDownloadBot/1.0", env="AIC_USER_AGENT")
    aic_rate_limit: float = Field(default=1.0, env="AIC_RATE_LIMIT")
    aic_use_data_dump: bool = Field(default=False, env="AIC_USE_DATA_DUMP")
    aic_data_dump_path: str = Field(
        default="artic-api-data/AIC_json/artworks", env="AIC_DATA_DUMP_PATH"
    )

    met_api_base_url: str = Field(
        default="https://collectionapi.metmuseum.org/public/collection/v1",
        env="MET_API_BASE_URL",
    )
    met_user_agent: str = Field(
        default="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        env="MET_USER_AGENT"
    )
    met_rate_limit: float = Field(default=2.0, env="MET_RATE_LIMIT")

    cma_api_base_url: str = Field(
        default="https://openaccess-api.clevelandart.org/api",
        env="CLEVELAND_API_BASE_URL",
    )
    cma_user_agent: str = Field(
        default="Cleveland-ArtDownloadBot/1.0", env="CLEVELAND_USER_AGENT"
    )
    cma_rate_limit: float = Field(default=80.0, env="CLEVELAND_RATE_LIMIT")
    cma_use_data_dump: bool = Field(default=False, env="CMA_USE_DATA_DUMP")
    cma_data_dump_path: str = Field(default="CMA_data.json", env="CMA_DATA_DUMP_PATH")

    mia_repo_url: str = Field(
        default="https://github.com/artsmia/collection.git",
        env="MIA_REPO_URL"
    )
    mia_repo_path: str = Field(
        default="mia/collection",
        env="MIA_REPO_PATH"
    )
    mia_user_agent: str = Field(
        default="MIA-ArtDownloadBot/1.0",
        env="MIA_USER_AGENT"
    )

    smk_api_base_url: str = Field(
        default="https://api.smk.dk/api/v1",
        env="SMK_API_BASE_URL",
    )
    smk_user_agent: str = Field(default="SMK-ArtDownloadBot/1.0", env="SMK_USER_AGENT")
    smk_rate_limit: float = Field(default=1.0, env="SMK_RATE_LIMIT")

    nga_user_agent: str = Field(default="NGA-ArtDownloadBot/1.0", env="NGA_USER_AGENT")
    nga_rate_limit: float = Field(default=5.0, env="NGA_RATE_LIMIT")

    museum_queries: MuseumQuerySettings = Field(
        default_factory=MuseumQuerySettings,
        description="Museum-specific query parameters",
    )

    def get_museum_info(self, museum_id: str) -> MuseumInfo:
        """Get MuseumInfo for a specific museum"""
        if museum_id not in self.museums:
            raise ValueError(f"Unknown museum ID: {museum_id}")

        config = self.museums[museum_id]
        return config.to_museum_info()

    @property
    def museums(self) -> Dict[str, MuseumConfig]:
        """Create museum configurations using the settings."""
        # Use shared museum metadata for names and base configuration
        return {
            "aic": MuseumConfig(
                api_base_url=self.aic_api_base_url,
                user_agent=self.aic_user_agent,
                rate_limit=self.aic_rate_limit,
                contact_email=self.default_contact_email,
                code="aic",
                name=SHARED_MUSEUMS["aic"].name,
                use_data_dump=self.aic_use_data_dump,
                data_dump_path=self.data_dir / self.aic_data_dump_path,
            ),
            "met": MuseumConfig(
                api_base_url=self.met_api_base_url,
                user_agent=self.met_user_agent,
                rate_limit=self.met_rate_limit,
                contact_email=self.default_contact_email,
                code="met",
                name=SHARED_MUSEUMS["met"].name,
            ),
            "cma": MuseumConfig(
                api_base_url=self.cma_api_base_url,
                user_agent=self.cma_user_agent,
                rate_limit=self.cma_rate_limit,
                contact_email=self.default_contact_email,
                code="cma",
                name=SHARED_MUSEUMS["cma"].name,
                use_data_dump=self.cma_use_data_dump,
                data_dump_path=self.data_dir / self.cma_data_dump_path,
            ),
            "mia": MuseumConfig(
                api_base_url="http://api.artsmia.org",  # For image URLs
                user_agent=self.mia_user_agent,
                rate_limit=0,  # No rate limit (local git repo)
                contact_email=self.default_contact_email,
                code="mia",
                name=SHARED_MUSEUMS["mia"].name,
                use_data_dump=True,  # Always use git repo
                data_dump_path=self.data_dir / self.mia_repo_path,
            ),
            "smk": MuseumConfig(
                api_base_url=self.smk_api_base_url,
                user_agent=self.smk_user_agent,
                rate_limit=self.smk_rate_limit,
                contact_email=self.default_contact_email,
                code="smk",
                name=SHARED_MUSEUMS["smk"].name,
            ),
            "nga": MuseumConfig(
                api_base_url="https://api.nga.gov/iiif",
                user_agent=self.nga_user_agent,
                rate_limit=self.nga_rate_limit,
                contact_email=self.default_contact_email,
                code="nga",
                name=SHARED_MUSEUMS["nga"].name,
                use_data_dump=True,
                data_dump_path=self.data_dir / "nga" / "csvs",
            ),
        }

    # File System Configuration
    project_root: Optional[Path] = None
    data_dir: Optional[Path] = None
    cache_dir: Optional[Path] = None
    images_dir: Optional[Path] = None
    logs_dir: Optional[Path] = None
    cache_file: Optional[Path] = None
    database_path: Optional[Path] = None

    # Museum-specific directories
    museum_dirs: Dict[str, Path] = Field(default_factory=dict)

    # Download Configuration
    batch_size: int = Field(default=100, env="BATCH_SIZE")
    rate_limit_delay: float = Field(default=1.0, env="RATE_LIMIT_DELAY")
    error_retry_delay: float = Field(default=5.0, env="ERROR_RETRY_DELAY")
    max_retries: int = Field(default=5, env="MAX_RETRIES")
    max_downloads: Optional[int] = None
    max_storage_gb: Optional[float] = None
    download_images: bool = Field(
        default=False,
        env="DOWNLOAD_IMAGES",
        description="Whether to download images (False = metadata only)",
    )

    def initialize_paths(self, project_root: Path) -> None:
        """Initialize path configurations based on project root."""
        self.project_root = project_root
        self.data_dir = project_root / "data"

        # Setup shared directories
        self.cache_dir = self.data_dir / "cache"
        self.logs_dir = self.data_dir / "logs"

        # Setup database
        self.database_path = self.data_dir / "artwork.db"

        # Museum specific directories
        self.museum_dirs = {
            "aic": self.data_dir / "aic",
            "met": self.data_dir / "met",
            "cma": self.data_dir / "cma",
            "mia": self.data_dir / "mia",
            "smk": self.data_dir / "smk",
            "nga": self.data_dir / "nga",
        }

        for museum, base_dir in self.museum_dirs.items():
            (base_dir / "images").mkdir(parents=True, exist_ok=True)
            (base_dir / "cache").mkdir(parents=True, exist_ok=True)

        # Ensure all directories exist
        self._ensure_directories()

    def get_museum_paths(self, museum_id: str) -> Dict[str, Path]:
        """Get paths for a specific museum"""
        if museum_id not in self.museum_dirs:
            raise ValueError(f"Unknown museum ID: {museum_id}")

        base_dir = self.museum_dirs[museum_id]
        museum_cache = base_dir / "cache"

        return {
            "base": base_dir,
            "images": base_dir / "images",
            "cache": museum_cache,
            "processed_ids": museum_cache / "processed_ids.json",
        }

    def _ensure_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        directories = [
            self.data_dir,
            self.cache_dir,
            self.logs_dir,
            *self.museum_dirs.values(),
        ]

        for directory in directories:
            if directory:  # Check if not None before creating
                directory.mkdir(parents=True, exist_ok=True)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "allow"


# Create global settings instance
settings = Settings()
