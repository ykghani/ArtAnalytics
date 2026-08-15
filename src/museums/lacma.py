"""LACMA (Los Angeles County Museum of Art) client.

Data source: `POST https://collections.lacma.org/api/search` — an unofficial,
undocumented same-origin JSON endpoint that powers the collections.lacma.org
search UI (found in the site's Next.js bundle). See docs/lacma.md for the
full writeup this implementation is based on.

Key constraints from that research:
  - No auth required.
  - `total` is hard-capped at 10000 for any query that actually matches more;
    paging past the reachable window silently clamps/repeats the last page
    rather than erroring, so pagination must be bounded independently.
  - Public-domain-with-image works total ~25,135 — too many for one query's
    10,000-result window, so the crawl is sliced by `department` (the
    largest department bucket is ~5,020, safely under the cap) and each
    slice is paginated with `page`/`perPage` on its own.
  - Department facet values are discovered at runtime from the `facets`
    block of a `perPage=1` request rather than hardcoded, so the crawl
    stays correct if LACMA adds/renames departments.
"""
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .base import MuseumAPIClient, MuseumImageProcessor
from ..config import settings
from ..download.progress_tracker import BaseProgressTracker, ProgressState
from .schemas import ArtworkMetadata, LACMAArtworkFactory, MuseumInfo
from ..utils import setup_logging

LACMA_PER_PAGE = 100
LACMA_MAX_RESULT_WINDOW = 10000  # server-enforced cap on total/page count per query

# Fallback used only if the live facets response has no usable `department`
# block (e.g. shape change) — keeps a department-sliced crawl possible.
# Source: docs/lacma.md §8 (facet counts measured live, all comfortably
# under the 10,000-per-query cap).
LACMA_FALLBACK_DEPARTMENTS: List[str] = [
    "Prints and Drawings",
    "Costume and Textiles",
    "Japanese Art",
    "South and Southeast Asian Art",
    "Art of the Middle East: Ancient",
    "Art of the Ancient Americas",
    "Decorative Arts and Design",
    "Egyptian Art",
    "Art of the Middle East: Islamic",
    "Photography",
    "Chinese and Korean Art",
    "European Painting and Sculpture",
    "Robert Gore Rifkind Center for German Expressionist Studies",
    "European Painting and Sculpture: Greek and Roman",
    "American Art",
    "Latin American Art",
    "Art of the Pacific",
    "Modern Art",
    "African Art",
]


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


@dataclass
class LACMAProgressState(ProgressState):
    """LACMA progress state — inherits 4 base fields, adds department-slice resume fields."""

    total_objects: int = 0
    department_index: int = 0
    last_page: int = 1


class LACMAProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        # Initialize state before calling super().__init__() since parent's _load_progress()
        # calls restore_state() which needs self.state to exist
        self.state = LACMAProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        # Override the parent's logger with museum-specific logger
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "lacma")

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "total_objects": self.state.total_objects,
            "department_index": self.state.department_index,
            "last_page": self.state.last_page,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", {})
        self.state.total_objects = data.get("total_objects", 0)
        self.state.department_index = data.get("department_index", 0)
        self.state.last_page = data.get("last_page", 1)


class LACMAClient(MuseumAPIClient):
    """Los Angeles County Museum of Art API Client implementation."""

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[BaseProgressTracker] = None,
    ):
        super().__init__(
            museum_info=museum_info, api_key=api_key, cache_file=cache_file
        )
        self.progress_tracker = progress_tracker
        self.artwork_factory = LACMAArtworkFactory()
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "lacma")

    def _get_auth_header(self) -> str:
        """LACMA's /api/search endpoint requires no authentication."""
        return ""

    def _search(self, page: int, per_page: int, department: List[str]) -> Dict[str, Any]:
        """POST one page of /api/search. `department: []` means "no department filter"."""
        body = {
            "query": "",
            "classification": [],
            "department": department,
            "artist": [],
            "placeMade": [],
            "creditLine": [],
            "building": [],
            "gallery": [],
            "onView": False,
            "hasImage": True,
            "publicDomain": True,
            "sort": "RELEVANCE",
            "page": page,
            "perPage": per_page,
        }
        response = self.session.post(self.museum_info.base_url, json=body, timeout=(10, 30))
        response.raise_for_status()
        return response.json()

    def _discover_departments(self) -> List[str]:
        """Read department facet values from a cheap perPage=1 request."""
        try:
            data = self._search(page=1, per_page=1, department=[])
            facets = data.get("facets") or {}
            dept_facet = facets.get("department") or []
            departments = [d.get("value") for d in dept_facet if d.get("value")]
            if departments:
                return departments
            self.logger.warning(
                "LACMA: department facet empty in search response; using fallback list"
            )
        except Exception as e:
            self.logger.error(f"LACMA: failed to discover departments, using fallback list: {e}")
        return list(LACMA_FALLBACK_DEPARTMENTS)

    def get_collection_info(self) -> Dict[str, Any]:
        """Sum department facet counts — the `total` field is capped at 10000 (see module docstring)."""
        data = self._search(page=1, per_page=1, department=[])
        facets = data.get("facets") or {}
        dept_facet = facets.get("department") or []
        total = sum(d.get("count", 0) for d in dept_facet)
        if not total:
            total = data.get("total", 0)
        return {"total_objects": total}

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        departments: List[str] = params.get("departments") or self._discover_departments()
        self.logger.progress(f"LACMA: crawling {len(departments)} department slices")

        start_dept_idx = 0
        start_page = 1
        if self.progress_tracker and isinstance(self.progress_tracker, LACMAProgressTracker):
            start_dept_idx = min(self.progress_tracker.state.department_index, len(departments))
            start_page = self.progress_tracker.state.last_page

        for dept_idx in range(start_dept_idx, len(departments)):
            department = departments[dept_idx]
            page = start_page if dept_idx == start_dept_idx else 1
            self.logger.progress(
                f"LACMA: department {dept_idx + 1}/{len(departments)} '{department}' (from page {page})"
            )
            yield from self._iter_department(department, dept_idx, start_page=page)

            if self.progress_tracker and isinstance(self.progress_tracker, LACMAProgressTracker):
                self.progress_tracker.state.department_index = dept_idx + 1
                self.progress_tracker.state.last_page = 1
                self.progress_tracker.force_save()

    def _iter_department(
        self, department: str, dept_idx: int, start_page: int = 1
    ) -> Iterator[ArtworkMetadata]:
        page = start_page
        max_pages: Optional[int] = None

        while True:
            try:
                data = self._search(page=page, per_page=LACMA_PER_PAGE, department=[department])
            except Exception as e:
                self.logger.error(f"LACMA: search failed for department '{department}' page {page}: {e}")
                break

            if max_pages is None:
                total = data.get("total", 0) or 0
                max_pages = max(
                    1,
                    min(
                        _ceil_div(total, LACMA_PER_PAGE),
                        _ceil_div(LACMA_MAX_RESULT_WINDOW, LACMA_PER_PAGE),
                    ),
                )
                if self.progress_tracker and isinstance(self.progress_tracker, LACMAProgressTracker):
                    self.progress_tracker.state.total_objects += total

            results = data.get("results") or []
            if not results:
                break

            for entry in results:
                artwork_id = entry.get("id")
                if artwork_id is None:
                    continue
                str_id = str(artwork_id)
                if self.progress_tracker and self.progress_tracker.is_processed(str_id):
                    continue

                metadata = self.artwork_factory.create_metadata(entry)
                if metadata:
                    yield metadata

            if self.progress_tracker and isinstance(self.progress_tracker, LACMAProgressTracker):
                self.progress_tracker.state.department_index = dept_idx
                self.progress_tracker.state.last_page = page + 1
                self.progress_tracker.force_save()

            if page >= max_pages or len(results) < LACMA_PER_PAGE:
                break

            page += 1
            time.sleep(self.museum_info.rate_limit)

    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        """Not supported: LACMA's /api/search has no id-based lookup (no documented
        `/api/object/{id}` JSON endpoint exists — see docs/lacma.md). Use
        iter_collection for bulk retrieval."""
        self.logger.warning(
            f"get_artwork_details not supported for LACMA (id '{artwork_id}'): "
            f"no id-based lookup endpoint is documented. Use iter_collection for bulk retrieval."
        )
        return None


class LACMAImageProcessor(MuseumImageProcessor):
    """LACMA image processor implementation.

    Uses the base class's generate_filename/process_image as-is: the default
    `LACMA_` prefix (museum_info.code.upper()) already matches what's required.
    """

    def __init__(self, output_dir: Path, museum_info: MuseumInfo):
        super().__init__(output_dir, museum_info)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "lacma")
