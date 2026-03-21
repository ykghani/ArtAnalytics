"""
Minneapolis Institute of Art (MIA) API client and image processor.

Data Source: Git repository (https://github.com/artsmia/collection)
- Metadata: JSON files organized in buckets (objects/{bucket}/{id}.json)
- Images: Scraped from https://collections.artsmia.org/art/{id}
- Updates: Daily git commits

Image CDN Pattern:
https://img.artsmia.org/web_objects_cache/{bucket1}/200/{bucket2}/{id}/mia_{hash}_full.jpg

Note: The hash component is obtained by scraping the collection page's download button.
"""

import json
import re
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Dict, Any, Optional, Iterator

import requests
from bs4 import BeautifulSoup
from PIL import Image

from .base import MuseumAPIClient, MuseumImageProcessor
from .museum_info import MuseumInfo
from .schemas import ArtworkMetadata, ArtworkMetadataFactory
from ..download.progress_tracker import BaseProgressTracker, ProgressState
from ..utils import sanitize_filename, setup_logging
from ..config import settings


from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Global browser instance for efficiency
_browser_context = None


def _get_browser_context():
    """Get or create a persistent browser context for scraping."""
    global _browser_context
    if _browser_context is None:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        _browser_context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    return _browser_context


def _verify_url_exists(url: str, timeout: int = 10) -> bool:
    """
    Verify that a URL exists and is accessible via HEAD request.

    Args:
        url: URL to check
        timeout: Request timeout in seconds

    Returns:
        True if URL returns 200 OK, False otherwise
    """
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        return response.status_code == 200
    except Exception:
        return False


@lru_cache(maxsize=1000)
def scrape_mia_image_url(artwork_id: str) -> Optional[str]:
    """
    Scrape the actual CDN image URL from MIA's collection page using Playwright.

    Strategy: Get the web-resolution URL (_800.jpg) from the page, then attempt
    to transform it to full-resolution (_full.jpg). Falls back to web version
    if full resolution is not available.

    The download button contains the full CDN URL with the hash component
    that's not available in the JSON metadata. Since the page is client-side
    rendered, we need a headless browser to execute JavaScript.

    Args:
        artwork_id: MIA object ID

    Returns:
        Full CDN URL (preferring _full.jpg, falling back to _800.jpg) or None if scraping fails

    Example:
        Input: "1218"
        Scraped: "https://img.artsmia.org/.../mia_8017891_800.jpg"
        Returns: "https://img.artsmia.org/.../mia_8017891_full.jpg" (if exists)
                 OR "https://img.artsmia.org/.../mia_8017891_800.jpg" (fallback)
    """
    collection_url = f"https://collections.artsmia.org/art/{artwork_id}"

    try:
        context = _get_browser_context()
        page = context.new_page()

        # Navigate to the artwork page
        page.goto(collection_url, wait_until="load", timeout=30000)

        # Give JavaScript time to fully execute and populate the href attribute
        # The page is client-side rendered and needs ~5 seconds
        time.sleep(5)

        # Find the download button with the CDN URL
        # Based on screenshot: <a class="material-icons" download="" href="https://...">
        download_button = page.query_selector('a[download][href*="img.artsmia.org"]')

        if download_button:
            cdn_url = download_button.get_attribute('href')
            page.close()

            # Validate basic URL structure
            if not cdn_url or 'img.artsmia.org' not in cdn_url:
                print(f"Warning: Download button found for artwork {artwork_id} but URL format invalid: {cdn_url}")
                return None

            # Check if URL has an image extension
            if not ('.jpg' in cdn_url.lower() or '.jpeg' in cdn_url.lower() or '.png' in cdn_url.lower()):
                print(f"Warning: Download button found for artwork {artwork_id} but URL is not an image: {cdn_url}")
                return None

            # Option 3: Try to get full resolution, fall back to web version
            # Transform _NNN.jpg (e.g., _800.jpg, _1024.jpg) to _full.jpg
            full_url = re.sub(r'_\d+\.(jpg|jpeg|png)$', r'_full.\1', cdn_url, flags=re.IGNORECASE)

            if full_url != cdn_url:
                # We successfully transformed the URL, now verify it exists
                print(f"Info: Checking if full resolution exists for artwork {artwork_id}...")
                if _verify_url_exists(full_url):
                    print(f"Success: Full resolution available for artwork {artwork_id}: {full_url}")
                    return full_url
                else:
                    print(f"Info: Full resolution not available for artwork {artwork_id}, using web version: {cdn_url}")
                    return cdn_url
            else:
                # URL didn't match the pattern (might already be _full.jpg or different format)
                # Just return it as-is
                return cdn_url

        page.close()
        print(f"Warning: No download button on MIA page for artwork {artwork_id} at {collection_url} (image may not be available on museum CDN)")
        return None

    except PlaywrightTimeoutError:
        print(f"Warning: Timeout (30s) loading MIA page for artwork {artwork_id} at {collection_url} (network/server issue)")
        try:
            page.close()
        except:
            pass
        return None
    except Exception as e:
        print(f"Warning: Scraping error for artwork {artwork_id}: {type(e).__name__}: {e}")
        try:
            page.close()
        except:
            pass
        return None


class MIAArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Minneapolis Institute of Art artwork metadata"""

    def __init__(self):
        super().__init__("mia")

    def create_metadata(self, data: Dict[str, Any]) -> Optional[ArtworkMetadata]:
        """
        Create ArtworkMetadata from MIA JSON data.

        Field Mappings:
        - id (from URL) → id
        - accession_number → accession_number
        - title → title
        - artist → artist
        - life_date → artist_display, artist_birth_year, artist_death_year
        - nationality → artist_nationality
        - dated → date_display
        - medium → medium
        - dimension → dimensions, height_cm, width_cm
        - department → department
        - classification → artwork_type
        - culture → culture
        - style → style
        - restricted → is_public_domain (0 = public domain)
        - creditline → credit_line
        - room → is_on_view (not "Not on View")
        - description → description
        - text → short_description
        - provenance → provenance
        - inscription + marks → inscriptions
        - image → determines if image exists
        - image_height/width → image_pixel_height/width
        - country + continent → keywords
        """

        if not data:
            self.logger.debug("Received empty data")
            return None

        # Extract ID from URL (http://api.artsmia.org/objects/17 → 17)
        # or use numeric id directly
        artwork_id = data.get("id")
        if isinstance(artwork_id, str) and "objects/" in artwork_id:
            artwork_id = artwork_id.split("/")[-1]
        artwork_id = str(artwork_id)

        if not artwork_id:
            self.logger.debug("No artwork ID found")
            return None

        # Only process artworks with valid images that are not restricted
        if data.get("image") != "valid":
            self.logger.progress(
                f"SKIP: Artwork {artwork_id} - image status is '{data.get('image')}' (must be 'valid')"
            )
            return None

        if data.get("restricted", 1) != 0:
            self.logger.progress(
                f"SKIP: Artwork {artwork_id} - not public domain (restricted={data.get('restricted')})"
            )
            return None


        # Filter for digital display-friendly artwork types
        # Only include 2D artworks that look good on screens
        allowed_classifications = {
            'Photographs', 'Prints', 'Drawings', 'Paintings', 'Calligraphy',
            'Works on Paper', 'Manuscript',
            # Include variations with leading spaces
            ' Photographs', ' Prints', ' Drawings', ' Paintings', ' Calligraphy',
            ' Works on Paper', ' Manuscript',
            # Include common subcategories
            'Drawings, Works on Paper', 'Prints, Works on Paper',
            'Paintings, Works on Paper', 'Photographs, Prints',
            'Drawings, Prints, Works on Paper', 'Prints, Drawings, Works on Paper',
            'Paintings, Drawings, Works on Paper', 'Drawings, Paintings, Works on Paper',
            'Paintings, Drawings', 'Drawings, Paintings',
            'Photographs, Works on Paper', 'Drawings, Photographs, Works on Paper',
            'Prints, Photographs, Works on Paper', 'Prints, Photographs',
            'Photographs, Collages / Assemblages',  # Photographic collages
            'Prints, Collages / Assemblages',  # Print collages
            'Drawings, Collages / Assemblages',  # Drawing collages
            'Collages / Assemblages, Works on Paper',  # Paper-based collages
            ' Paintings; Calligraphy', 'Paintings, Calligraphy',
            ' Drawings; Works on Paper',
            'Books, Prints', ' Prints; Books', 'Books, Drawings',
            'Prints, Books', 'Drawings, Books',
        }
        
        classification = data.get('classification', '').strip()
        if classification and classification not in allowed_classifications:
            self.logger.progress(
                f"SKIP: Artwork {artwork_id} - classification '{classification}' not suitable for digital display (3D object or non-artwork)"
            )
            return None
        try:
            # Parse artist birth/death years from life_date
            # Format: "Dutch, 1853–1890" or "American, 1838 - 1909"
            life_date = data.get("life_date", "")
            artist_birth_year, artist_death_year = self._parse_life_dates(life_date)

            # Parse dimensions from dimension string
            # Format: "3 1/4 x 7 1/2 in. (8.26 x 19.05 cm)"
            dimension_str = data.get("dimension", "")
            height_cm, width_cm = self._parse_dimensions(dimension_str)

            # Determine if on view
            room = data.get("room", "")
            is_on_view = bool(room and room != "Not on View")

            # Collect inscriptions from multiple fields
            inscriptions = []
            if data.get("inscription"):
                inscriptions.append(data.get("inscription"))
            if data.get("marks"):
                inscriptions.append(data.get("marks"))
            if data.get("markings"):
                inscriptions.append(data.get("markings"))
            if data.get("signed"):
                inscriptions.append(data.get("signed"))

            # Build keywords from geography
            keywords = []
            if data.get("country"):
                keywords.append(data.get("country"))
            if data.get("continent"):
                keywords.append(data.get("continent"))

            # Scrape the actual CDN image URL from the collection page
            # This is necessary because the JSON doesn't contain the hash component
            # Example: https://img.artsmia.org/web_objects_cache/001000/200/10/1218/mia_8017891_full.jpg
            self.logger.progress(
                f"PROCESS: Artwork {artwork_id} - '{data.get('title', 'Untitled')}' by {data.get('artist', 'Unknown')} [{classification}]"
            )
            primary_image_url = scrape_mia_image_url(artwork_id)

            # Skip artworks where we can't find a valid image URL
            if not primary_image_url:
                self.logger.progress(
                    f"SKIP: Artwork {artwork_id} - no downloadable image URL found on museum website"
                )
                return None

            self.logger.progress(
                f"SUCCESS: Artwork {artwork_id} accepted for download"
            )
            return ArtworkMetadata(
                id=artwork_id,
                accession_number=data.get("accession_number", ""),
                title=data.get("title", "Untitled"),
                artist=data.get("artist", "Unknown"),
                artist_display=life_date,
                artist_bio=None,  # Not provided by MIA
                artist_nationality=data.get("nationality"),
                artist_birth_year=artist_birth_year,
                artist_death_year=artist_death_year,
                date_display=data.get("dated"),
                date_start=None,  # MIA doesn't provide numeric date ranges
                date_end=None,
                medium=data.get("medium"),
                dimensions=dimension_str,
                height_cm=height_cm,
                width_cm=width_cm,
                depth_cm=None,  # Would need complex parsing
                diameter_cm=None,
                department=data.get("department"),
                artwork_type=data.get("classification") or data.get("object_name"),
                culture=[data.get("culture")] if data.get("culture") else [],
                style=data.get("style"),
                is_public_domain=data.get("restricted", 1) == 0,
                credit_line=data.get("creditline"),
                is_on_view=is_on_view,
                is_highlight=None,  # Not provided by MIA
                is_boosted=None,
                boost_rank=None,
                has_not_been_viewed_much=None,
                description=data.get("description"),
                short_description=data.get("text"),
                provenance=data.get("provenance"),
                inscriptions=inscriptions,
                fun_fact=None,
                style_titles=[],
                keywords=keywords,
                primary_image_url=primary_image_url,
                image_urls={},  # MIA only provides one full-resolution CDN URL
                colorfulness=None,
                color_h=None,
                color_s=None,
                color_l=None,
                image_pixel_height=data.get("image_height"),
                image_pixel_width=data.get("image_width"),
            )

        except Exception as e:
            self.logger.error(
                f"Error creating metadata for artwork {artwork_id}: {str(e)}"
            )
            return None

    def _parse_life_dates(
        self, life_date: str
    ) -> tuple[Optional[int], Optional[int]]:
        """
        Parse birth and death years from life_date string.

        Examples:
        - "Dutch, 1853–1890" → (1853, 1890)
        - "American, 1838 - 1909" → (1838, 1909)
        - "French, born 1945" → (1945, None)
        - "American" → (None, None)
        """
        if not life_date:
            return None, None

        # Match patterns like "1853–1890" or "1838 - 1909"
        date_range_pattern = r"(\d{4})\s*[-–—]\s*(\d{4})"
        match = re.search(date_range_pattern, life_date)
        if match:
            return int(match.group(1)), int(match.group(2))

        # Match patterns like "born 1945"
        birth_pattern = r"born\s+(\d{4})"
        match = re.search(birth_pattern, life_date)
        if match:
            return int(match.group(1)), None

        # Match single year (e.g., "1890")
        single_year_pattern = r"(\d{4})"
        match = re.search(single_year_pattern, life_date)
        if match:
            # Ambiguous - could be birth or death year
            # Conservatively return as birth year
            return int(match.group(1)), None

        return None, None

    def _parse_dimensions(self, dimension_str: str) -> tuple[Optional[float], Optional[float]]:
        """
        Parse height and width in cm from dimension string.

        Example:
        - "3 1/4 x 7 1/2 in. (8.26 x 19.05 cm)" → (8.26, 19.05)
        - "29 x 36 1/2 in. (73.66 x 92.71 cm) (canvas)" → (73.66, 92.71)

        Strategy: Extract first occurrence of "(X x Y cm)"
        """
        if not dimension_str:
            return None, None

        # Match pattern: (number x number cm)
        # Handles formats like (8.26 x 19.05 cm) or (73.66 x 92.71 cm)
        cm_pattern = r"\((\d+\.?\d*)\s*x\s*(\d+\.?\d*)\s*cm\)"
        match = re.search(cm_pattern, dimension_str)

        if match:
            try:
                height = float(match.group(1))
                width = float(match.group(2))
                return height, width
            except ValueError:
                return None, None

        return None, None


@dataclass
class MIAProgressState(ProgressState):
    """Progress tracking state for MIA collection processing"""

    last_bucket: int = -1  # Last processed bucket (0, 1, 2, ...)
    last_object_id: str = ""  # Last processed object ID within bucket
    total_buckets: int = 0  # Total number of buckets
    repo_commit_hash: str = ""  # Git commit hash when processing started


class MIAProgressTracker(BaseProgressTracker):
    """Progress tracker for MIA git repository processing"""

    def __init__(self, progress_file: Path):
        self.state = MIAProgressState()
        super().__init__(progress_file)

    def get_state_dict(self) -> Dict[str, Any]:
        """Serialize state to dictionary for JSON storage"""
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "last_bucket": self.state.last_bucket,
            "last_object_id": self.state.last_object_id,
            "total_buckets": self.state.total_buckets,
            "repo_commit_hash": self.state.repo_commit_hash,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        """Restore state from dictionary"""
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", [])
        self.state.last_bucket = data.get("last_bucket", -1)
        self.state.last_object_id = data.get("last_object_id", "")
        self.state.total_buckets = data.get("total_buckets", 0)
        self.state.repo_commit_hash = data.get("repo_commit_hash", "")


class MIAClient(MuseumAPIClient):
    """
    Minneapolis Institute of Art API client.

    Unlike other museums, MIA uses a git repository for metadata distribution.
    This client:
    1. Clones/pulls the collection repository
    2. Walks the objects/ directory structure
    3. Parses JSON files to extract metadata
    """

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[MIAProgressTracker] = None,
        repo_path: Optional[Path] = None,
        repo_url: str = "https://github.com/artsmia/collection.git",
    ):
        super().__init__(museum_info, api_key, cache_file)
        self.progress_tracker = progress_tracker
        self.artwork_factory = MIAArtworkFactory()
        self.repo_path = repo_path or Path("data/mia/collection")
        self.repo_url = repo_url
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "mia")

    def _get_auth_header(self) -> str:
        """No authentication required for MIA"""
        return ""

    def _ensure_repo_ready(self) -> None:
        """Clone or update the MIA collection repository"""
        if not self.repo_path.exists():
            self.logger.progress(
                f"Cloning MIA collection repository to {self.repo_path}..."
            )
            self.repo_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", self.repo_url, str(self.repo_path)],
                check=True,
                capture_output=True,
            )
            self.logger.progress("Repository cloned successfully")
        else:
            self.logger.progress("Updating MIA collection repository...")
            subprocess.run(
                ["git", "-C", str(self.repo_path), "pull"],
                check=True,
                capture_output=True,
            )
            self.logger.progress("Repository updated successfully")

        # Store current commit hash for tracking
        result = subprocess.run(
            ["git", "-C", str(self.repo_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        commit_hash = result.stdout.strip()
        if self.progress_tracker:
            self.progress_tracker.state.repo_commit_hash = commit_hash
            self.logger.progress(f"Processing repository at commit {commit_hash[:8]}")

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        """
        Iterate through MIA collection by walking git repository.

        Yields:
            ArtworkMetadata objects for each valid artwork
        """
        self._ensure_repo_ready()

        objects_dir = self.repo_path / "objects"
        if not objects_dir.exists():
            raise FileNotFoundError(f"Objects directory not found: {objects_dir}")

        # Get all bucket directories (sorted numerically)
        bucket_dirs = sorted(
            [d for d in objects_dir.iterdir() if d.is_dir()],
            key=lambda x: int(x.name) if x.name.isdigit() else 0,
        )

        if self.progress_tracker:
            self.progress_tracker.state.total_buckets = len(bucket_dirs)

        self.logger.info(f"Found {len(bucket_dirs)} buckets to process")

        # Resume from last bucket if available
        start_bucket = (
            self.progress_tracker.state.last_bucket + 1
            if self.progress_tracker
            else 0
        )

        for bucket_dir in bucket_dirs[start_bucket:]:
            bucket_num = int(bucket_dir.name)
            self.logger.progress(
                f"Processing bucket {bucket_num} ({bucket_num + 1}/{len(bucket_dirs)})"
            )

            # Get all JSON files in bucket (sorted numerically by ID)
            json_files = sorted(
                bucket_dir.glob("*.json"),
                key=lambda x: int(x.stem) if x.stem.isdigit() else 0,
            )

            for json_file in json_files:
                object_id = json_file.stem

                # Skip if already processed
                if self.progress_tracker and self.progress_tracker.is_processed(
                    object_id
                ):
                    continue

                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    # Create metadata
                    metadata = self.artwork_factory.create_metadata(data)

                    if metadata:
                        # Update progress tracking
                        if self.progress_tracker:
                            self.progress_tracker.state.last_bucket = bucket_num
                            self.progress_tracker.state.last_object_id = object_id

                        yield metadata
                    # Factory logs specific skip reasons, no need to log here

                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to parse {json_file}: {e}")
                    if self.progress_tracker:
                        self.progress_tracker.log_status(
                            object_id, "failed", f"JSON parse error: {e}"
                        )

                except Exception as e:
                    self.logger.error(
                        f"Error processing artwork {object_id}: {e}", exc_info=True
                    )
                    if self.progress_tracker:
                        self.progress_tracker.log_status(object_id, "failed", str(e))

    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        """
        Get details for a single artwork by ID.

        Args:
            artwork_id: Numeric object ID

        Returns:
            ArtworkMetadata or None if not found
        """
        self._ensure_repo_ready()

        # Calculate bucket: id / 1000
        bucket = int(artwork_id) // 1000
        json_file = self.repo_path / "objects" / str(bucket) / f"{artwork_id}.json"

        if not json_file.exists():
            self.logger.error(f"Artwork {artwork_id} not found at {json_file}")
            return None

        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return self.artwork_factory.create_metadata(data)

        except Exception as e:
            self.logger.error(f"Error loading artwork {artwork_id}: {e}")
            return None

    def get_collection_info(self) -> Dict[str, Any]:
        """
        Get information about the MIA collection.

        Returns:
            Dictionary with collection statistics
        """
        self._ensure_repo_ready()

        objects_dir = self.repo_path / "objects"
        total_objects = sum(1 for _ in objects_dir.rglob("*.json"))

        return {
            "total_objects": total_objects,
            "repository": self.repo_url,
            "local_path": str(self.repo_path),
            "commit_hash": self.progress_tracker.state.repo_commit_hash
            if self.progress_tracker
            else None,
        }


class MIAImageProcessor(MuseumImageProcessor):
    """Image processor for MIA artworks"""

    def __init__(self, output_dir: Path, museum_info: MuseumInfo):
        super().__init__(output_dir, museum_info)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "mia")

    def process_image(
        self, image_data: bytes, metadata: ArtworkMetadata
    ) -> tuple[Path, int, int]:
        """
        Process and save MIA artwork image.

        Args:
            image_data: Raw image bytes
            metadata: Artwork metadata

        Returns:
            Tuple of (filepath, width, height)
        """
        image = Image.open(BytesIO(image_data))
        width, height = image.size

        # Generate filename
        filename = self.generate_filename(metadata)
        filepath = self.output_dir / filename

        # Save as JPEG
        if image.mode in ("RGBA", "LA", "P"):
            # Convert to RGB for JPEG compatibility
            image = image.convert("RGB")

        image.save(filepath, format="JPEG", quality=95)

        self.logger.info(
            f"Saved image for {metadata.id}: {filename} ({width}x{height})"
        )

        return filepath, width, height

    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        """
        Generate sanitized filename for MIA artwork.

        Format: MIA_{id}_[title]_[artist].jpg
        """
        return sanitize_filename(
            id=f"MIA_{metadata.id}",
            title=metadata.title,
            artist=metadata.artist,
            max_length=255,
        )
