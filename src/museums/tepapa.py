"""Te Papa Tongarewa (Museum of New Zealand) museum client.

API: https://data.tepapa.govt.nz/collection
  - Requires free API key: https://data.tepapa.govt.nz/docs/
  - Set env var: TEPAPA_API_KEY=your_key
  - Auth method: x-api-key request header
  - GET /object?q={art_type} for each art type
  - Rights filter: "No Known Copyright Restrictions" or "CC 0" = public domain
  - Collections targeted: Art, Photography, TaongaMaori, PacificCultures
  - hasRepresentation items ARE the media objects (contentUrl is direct, not nested)
"""
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

from PIL import Image

from .base import MuseumAPIClient, MuseumImageProcessor
from .schemas import ArtworkMetadata, ArtworkMetadataFactory, MuseumInfo
from ..config import settings
from ..download.progress_tracker import BaseProgressTracker
from ..utils import sanitize_filename, setup_logging

# Art-type queries — each returns results under or near 50K so they can be fully paginated.
# Larger types (drawings, prints, photographs) are included but capped at the API's 50K limit.
ART_QUERIES = [
    "paintings",
    "watercolours",
    "watercolors",
    "sketches",
    "engravings",
    "etchings",
    "lithographs",
    "posters",
    "portraits",
    "pastels",
    "gouaches",
    "drawings",   # 54K — paginated to limit
    "prints",     # 90K — paginated to limit
    "photographs", # 130K — paginated to limit
]

# Only keep images from these collections
ART_COLLECTIONS = {"Art", "Photography", "TaongaMaori", "PacificCultures", "History", "Plants", "Insects"}

# Minimum image dimension (pixels) for screen-worthy display quality
MIN_DIMENSION = 1000

PUBLIC_DOMAIN_RIGHTS = ("no known copyright", "cc 0", "cc0", "public domain mark")


def _is_public_domain(rights_title: str) -> bool:
    lower = rights_title.lower()
    return any(pd in lower for pd in PUBLIC_DOMAIN_RIGHTS)


def _extract_best_representation(representations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the best downloadable public-domain representation.

    The hasRepresentation items ARE the media objects — contentUrl is directly
    on the rep, not nested under a 'media' sub-key.
    """
    best = None
    best_pixels = 0
    for rep in representations or []:
        rights = rep.get("rights") or {}
        if not rights.get("allowsDownload"):
            continue
        if not _is_public_domain(rights.get("title", "")):
            continue
        content_url = rep.get("contentUrl")
        if not content_url:
            continue
        w = rep.get("width") or 0
        h = rep.get("height") or 0
        if w < MIN_DIMENSION and h < MIN_DIMENSION:
            continue
        pixels = w * h
        if pixels > best_pixels:
            best = rep
            best_pixels = pixels
    return best


def _extract_artist(production: List[Dict[str, Any]]) -> str:
    if not production:
        return "Unknown Artist"
    first = production[0]
    contributor = first.get("contributor") or {}
    return contributor.get("title", "Unknown Artist") or "Unknown Artist"


def _extract_date(production: List[Dict[str, Any]], data: Dict[str, Any]) -> Optional[str]:
    if production:
        p = production[0]
        verbatim = p.get("verbatimCreatedDate") or p.get("createdDate")
        if verbatim:
            return str(verbatim)
    return data.get("date")


def _extract_type(data: Dict[str, Any]) -> Optional[str]:
    type_of = data.get("isTypeOf") or []
    labels = [c.get("prefLabel", "") for c in type_of if c.get("prefLabel")]
    return ", ".join(labels) if labels else data.get("type")


class TePapaArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Te Papa Tongarewa artwork metadata."""

    def __init__(self):
        super().__init__("tepapa")

    def create_metadata(self, data: Dict[str, Any]) -> Optional[ArtworkMetadata]:
        try:
            obj_id = data.get("id")
            if not obj_id:
                return None

            collection = data.get("collection") or ""
            if isinstance(collection, list):
                collection = collection[0] if collection else ""

            if collection not in ART_COLLECTIONS:
                return None

            representations = data.get("hasRepresentation") or []
            rep = _extract_best_representation(representations)
            if rep is None:
                return None

            content_url = rep.get("contentUrl")
            rights_title = (rep.get("rights") or {}).get("title", "")

            title = data.get("title", "Untitled") or "Untitled"
            production = data.get("production") or []
            artist = _extract_artist(production)
            date_display = _extract_date(production, data)
            artwork_type = _extract_type(data)

            keywords = [
                s.get("value", "") or s.get("prefLabel", "")
                for s in (data.get("subject") or [])
                if s.get("value") or s.get("prefLabel")
            ]

            return ArtworkMetadata(
                id=str(obj_id),
                accession_number=data.get("identifier") or str(obj_id),
                title=title,
                artist=artist,
                artist_display=artist,
                date_display=date_display,
                artwork_type=artwork_type,
                description=data.get("description"),
                keywords=keywords,
                is_public_domain=True,
                credit_line=rights_title or None,
                primary_image_url=content_url,
                image_urls={"full": content_url},
                image_pixel_width=rep.get("width"),
                image_pixel_height=rep.get("height"),
            )
        except Exception as e:
            self.logger.error(f"Error creating metadata for Te Papa object {data.get('id')}: {e}")
            return None


@dataclass
class TePapaProgressState:
    """State for Te Papa download progress tracking."""

    processed_ids: Set[str] = field(default_factory=set)
    success_ids: Set[str] = field(default_factory=set)
    failed_ids: Set[str] = field(default_factory=set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory=dict)
    current_query_index: int = 0
    current_offset: int = 0
    total_objects: int = 0


class TePapaProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        self.state = TePapaProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "tepapa")

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "current_query_index": self.state.current_query_index,
            "current_offset": self.state.current_offset,
            "total_objects": self.state.total_objects,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", {})
        self.state.current_query_index = data.get("current_query_index", 0)
        self.state.current_offset = data.get("current_offset", 0)
        self.state.total_objects = data.get("total_objects", 0)


class TePapaClient(MuseumAPIClient):
    """Te Papa Tongarewa (Museum of New Zealand) API Client.

    Iterates through a set of art-type queries against GET /object.
    Filters for public-domain items in art-relevant collections with
    high-resolution images suitable for screen display.
    """

    # Hard ceiling imposed by the API's Elasticsearch configuration
    API_PAGINATION_LIMIT = 49900

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[BaseProgressTracker] = None,
    ):
        super().__init__(museum_info=museum_info, api_key=None, cache_file=cache_file)
        self.progress_tracker = progress_tracker
        self.artwork_factory = TePapaArtworkFactory()
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "tepapa")
        if api_key:
            self.session.headers.update({
                "x-api-key": api_key,
                "Accept": "application/json;profiles=tepapa.collections.api.v3",
            })

    def _get_auth_header(self) -> str:
        return ""

    def get_collection_info(self) -> Dict[str, Any]:
        resp = self.session.get(
            f"{self.museum_info.base_url}/object",
            params={"size": 1},
            timeout=30,
        )
        resp.raise_for_status()
        meta = resp.json().get("_metadata") or {}
        total = (meta.get("resultset") or {}).get("count", 0)
        return {"total_objects": total}

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        tracker = self.progress_tracker
        start_query_idx = 0
        start_offset = 0
        if tracker and isinstance(tracker, TePapaProgressTracker):
            start_query_idx = tracker.state.current_query_index
            start_offset = tracker.state.current_offset

        for query_idx in range(start_query_idx, len(ART_QUERIES)):
            query = ART_QUERIES[query_idx]

            # Reset offset when moving to a new query
            offset = start_offset if query_idx == start_query_idx else 0
            start_offset = 0  # Only use saved offset for the first resumed query

            self.logger.info(f"Te Papa: starting query '{query}' from offset {offset}")

            # Probe total for this query
            probe = self.session.get(
                f"{self.museum_info.base_url}/object",
                params={"q": query, "size": 1, "from": 0},
                timeout=30,
            )
            probe.raise_for_status()
            probe_meta = probe.json().get("_metadata") or {}
            query_total = min(
                (probe_meta.get("resultset") or {}).get("count", 0),
                self.API_PAGINATION_LIMIT,
            )
            self.logger.info(f"Te Papa query '{query}': {query_total} items (capped at {self.API_PAGINATION_LIMIT})")

            page_size = 100
            while offset < query_total:
                resp = self.session.get(
                    f"{self.museum_info.base_url}/object",
                    params={"q": query, "size": page_size, "from": offset},
                    timeout=60,
                )
                resp.raise_for_status()
                items = resp.json().get("results") or []
                if not items:
                    break

                for item in items:
                    item_id = str(item.get("id", ""))
                    if not item_id:
                        continue
                    if tracker and tracker.is_processed(item_id):
                        continue
                    metadata = self.artwork_factory.create_metadata(item)
                    if metadata:
                        yield metadata

                offset += page_size

                if tracker and isinstance(tracker, TePapaProgressTracker):
                    tracker.state.current_query_index = query_idx
                    tracker.state.current_offset = offset
                    tracker._save_progress()

                self.logger.progress(
                    f"Te Papa query '{query}': offset {offset}/{query_total}"
                )
                time.sleep(self.museum_info.rate_limit)

            # Move to next query
            if tracker and isinstance(tracker, TePapaProgressTracker):
                tracker.state.current_query_index = query_idx + 1
                tracker.state.current_offset = 0
                tracker._save_progress()

        self.logger.info("Te Papa: all art queries complete")

    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        url = f"{self.museum_info.base_url}/object/{artwork_id}"
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return self.artwork_factory.create_metadata(resp.json())


class TePapaImageProcessor(MuseumImageProcessor):
    """Image processor for Te Papa Tongarewa artworks."""

    def process_image(self, image_data: bytes, metadata: ArtworkMetadata) -> tuple[Path, int, int]:
        try:
            image = Image.open(BytesIO(image_data))
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGB")
            width, height = image.size
            filename = self.generate_filename(metadata)
            filepath = self.output_dir / filename
            image.save(filepath, format="JPEG", quality=95)
            return filepath, width, height
        except Exception as e:
            raise RuntimeError(f"Failed to process Te Papa object {metadata.id}: {e}")

    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        return sanitize_filename(
            id=f"TePapa_{metadata.id}",
            title=metadata.title,
            artist=metadata.artist,
            max_length=255,
        )
