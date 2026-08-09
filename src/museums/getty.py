"""J. Paul Getty Museum client.

Collection: Getty Open Content Program (https://data.getty.edu/museum/collection/).
Platform: Linked.Art (CIDOC-CRM JSON-LD) REST + SPARQL, images via IIIF Image API.

There is no REST list/search endpoint (docs/getty.md §2) — the practical way
to enumerate public-domain artworks is the SPARQL endpoint, filtering for
`HumanMadeObject`s whose metadata rights are CC0 and that have a linked
visual item, paged with LIMIT/OFFSET (§2b). Each resulting object IRI is then
dereferenced individually as JSON (§2c/§3), and its linked `media/image`
resource is fetched separately to get IIIF access points and the *image*
rights block (§5/§6) — metadata CC0 does not imply the image itself is CC0.

No authentication required and no documented rate limit (§7/§9); a modest
per-item delay is still applied to be polite.
"""
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from .base import MuseumAPIClient, MuseumImageProcessor
from .schemas import ArtworkMetadata, GETTYArtworkFactory, MuseumInfo, getty_find_image_ref
from ..config import settings
from ..download.progress_tracker import BaseProgressTracker, ProgressState
from ..utils import setup_logging

GETTY_SPARQL_PAGE_SIZE = 200


def _getty_sparql_page_query(limit: int, offset: int) -> str:
    return (
        "PREFIX crm: <http://www.cidoc-crm.org/cidoc-crm/>\n"
        "SELECT ?obj WHERE {\n"
        "  ?obj a crm:E22_Human-Made_Object .\n"
        "  ?obj crm:P104_is_subject_to ?right .\n"
        "  ?right crm:P2_has_type <http://creativecommons.org/publicdomain/zero/1.0/> .\n"
        "  ?obj crm:P65_shows_visual_item ?vi .\n"
        "}\n"
        f"ORDER BY ?obj\nLIMIT {limit} OFFSET {offset}\n"
    )


GETTY_SPARQL_COUNT_QUERY = (
    "PREFIX crm: <http://www.cidoc-crm.org/cidoc-crm/>\n"
    "SELECT (COUNT(DISTINCT ?obj) AS ?count) WHERE {\n"
    "  ?obj a crm:E22_Human-Made_Object .\n"
    "  ?obj crm:P104_is_subject_to ?right .\n"
    "  ?right crm:P2_has_type <http://creativecommons.org/publicdomain/zero/1.0/> .\n"
    "  ?obj crm:P65_shows_visual_item ?vi .\n"
    "}\n"
)


class GETTYClient(MuseumAPIClient):
    """J. Paul Getty Museum Linked.Art / SPARQL client implementation."""

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[BaseProgressTracker] = None,
    ):
        super().__init__(museum_info=museum_info, api_key=api_key, cache_file=cache_file)
        self.progress_tracker = progress_tracker
        self.artwork_factory = GETTYArtworkFactory()
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "getty")
        self.sparql_url = f"{self.museum_info.base_url}/sparql"

    def _get_auth_header(self) -> str:
        """Getty does not require authentication (docs/getty.md §7)."""
        return ""

    def _customize_session(self, session) -> None:
        session.headers.update({"Accept": "application/json"})

    def _sparql_query(self, query: str) -> Dict[str, Any]:
        response = self.session.get(
            self.sparql_url,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            timeout=(10, 60),
        )
        response.raise_for_status()
        return response.json()

    def get_collection_info(self) -> Dict[str, Any]:
        try:
            data = self._sparql_query(GETTY_SPARQL_COUNT_QUERY)
            bindings = data.get("results", {}).get("bindings", [])
            total = int(bindings[0]["count"]["value"]) if bindings else 0
        except Exception as e:
            self.logger.error(f"Error fetching Getty collection count: {e}")
            total = 0
        return {"total_objects": total}

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        offset = 0
        if self.progress_tracker and hasattr(self.progress_tracker.state, "last_offset"):
            offset = self.progress_tracker.state.last_offset

        self.logger.info(f"Getty: starting SPARQL discovery at offset {offset}")

        while True:
            query = _getty_sparql_page_query(GETTY_SPARQL_PAGE_SIZE, offset)
            try:
                data = self._sparql_query(query)
            except Exception as e:
                self.logger.error(f"Getty: SPARQL query failed at offset {offset}: {e}")
                break

            bindings = data.get("results", {}).get("bindings", [])
            if not bindings:
                self.logger.info(f"Getty: no more results at offset {offset}, stopping")
                break

            for binding in bindings:
                object_uri = (binding.get("obj") or {}).get("value")
                if not object_uri:
                    continue
                artwork_id = object_uri.rstrip("/").split("/")[-1]

                if self.progress_tracker and self.progress_tracker.is_processed(artwork_id):
                    continue

                try:
                    artwork = self._get_artwork_details_impl(artwork_id)
                except Exception as e:
                    self.logger.error(f"Getty: error processing object {artwork_id}: {e}")
                    continue

                if artwork:
                    yield artwork

                if self.museum_info.rate_limit:
                    time.sleep(self.museum_info.rate_limit)

            offset += GETTY_SPARQL_PAGE_SIZE
            if self.progress_tracker and hasattr(self.progress_tracker.state, "last_offset"):
                self.progress_tracker.note_offset(offset)
                self.progress_tracker.force_save()

            if len(bindings) < GETTY_SPARQL_PAGE_SIZE:
                self.logger.info("Getty: reached last SPARQL page, stopping")
                break

    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        """Fetch an object record and, if it references one, its linked
        media/image resource (§4/§6), then build metadata from both."""
        object_url = f"{self.museum_info.base_url}/object/{artwork_id}"

        response = self.session.get(object_url, timeout=(10, 30))
        response.raise_for_status()
        object_data = response.json()

        image_data = None
        image_ref = getty_find_image_ref(object_data)
        if image_ref:
            try:
                image_response = self.session.get(image_ref, timeout=(10, 30))
                image_response.raise_for_status()
                image_data = image_response.json()
            except Exception as e:
                self.logger.debug(f"Getty: could not fetch image resource {image_ref}: {e}")

        return self.artwork_factory.create_metadata(object_data, image_data)


class GETTYImageProcessor(MuseumImageProcessor):
    """J. Paul Getty Museum image processor implementation.

    Uses the base class's default `generate_filename`/`process_image` — the
    default filename prefix (`museum_info.code.upper()` == "GETTY") already
    matches, so no override is needed here.
    """

    def __init__(self, output_dir: Path, museum_info: MuseumInfo):
        super().__init__(output_dir, museum_info)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "getty")


@dataclass
class GETTYProgressState(ProgressState):
    """Getty progress state — inherits 4 base fields, adds SPARQL resume fields."""

    total_objects: int = 0
    last_offset: int = 0


class GETTYProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        # Initialize state before calling super().__init__() since parent's _load_progress()
        # calls restore_state() which needs self.state to exist
        self.state = GETTYProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        # Override the parent's logger with museum-specific logger
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "getty")

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "total_objects": self.state.total_objects,
            "last_offset": self.state.last_offset,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", {})
        self.state.total_objects = data.get("total_objects", 0)
        self.state.last_offset = data.get("last_offset", 0)

    def note_offset(self, offset: int, *, total: int = 0) -> None:
        self.state.last_offset = offset
        if total:
            self.state.total_objects = total
