"""Rijksmuseum Amsterdam museum client.

API: OAI-PMH  https://data.rijksmuseum.nl/oai
  - No API key required
  - metadataPrefix: edm (EDM/RDF-XML)
  - Pagination via resumption tokens (non-expiring)
  - Rights filter: publicdomain / CC0 only
"""
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from PIL import Image

from .base import MuseumAPIClient, MuseumImageProcessor
from .schemas import ArtworkMetadata, ArtworkMetadataFactory, MuseumInfo
from ..config import settings
from ..download.progress_tracker import BaseProgressTracker
from ..utils import sanitize_filename, setup_logging

RIJKS_OAI_URL = "https://data.rijksmuseum.nl/oai"

NS = {
    'oai':     'http://www.openarchives.org/OAI/2.0/',
    'dc':      'http://purl.org/dc/elements/1.1/',
    'dcterms': 'http://purl.org/dc/terms/',
    'edm':     'http://www.europeana.eu/schemas/edm/',
    'ore':     'http://www.openarchives.org/ore/terms/',
    'rdf':     'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'skos':    'http://www.w3.org/2004/02/skos/core#',
}

_DIM_RE = re.compile(
    r'height\s+([\d.]+)\s*cm'
    r'|'
    r'width\s+([\d.]+)\s*cm',
    re.IGNORECASE,
)


def _parse_dimensions_edm(extent: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    """Parse 'height 363 cm × width 437 cm' → (363.0, 437.0).

    Returns (height_cm, width_cm). Either value is None when not found.
    Only centimetre values are recognised; 'mm' strings return (None, None).
    """
    if not extent:
        return None, None
    h = w = None
    for m in _DIM_RE.finditer(extent):
        if m.group(1) is not None:
            h = float(m.group(1))
        elif m.group(2) is not None:
            w = float(m.group(2))
    return h, w


def _is_public_domain_rights(rights_uri: Optional[str]) -> bool:
    """Return True when the EDM rights URI indicates public domain / CC0."""
    if not rights_uri:
        return False
    return "publicdomain" in rights_uri or "/zero/" in rights_uri


def _xml_record_to_dict(record_el: ET.Element) -> Dict[str, Any]:
    """Parse a single OAI <record> element into a plain dict for the factory."""
    # ── OAI header ────────────────────────────────────────────────────────────
    header = record_el.find("oai:header", NS)
    oai_id = ""
    if header is not None:
        id_el = header.find("oai:identifier", NS)
        oai_id = (id_el.text or "").strip() if id_el is not None else ""

    # ── Locate rdf:RDF ────────────────────────────────────────────────────────
    meta = record_el.find("oai:metadata", NS)
    rdf = meta.find("rdf:RDF", NS) if meta is not None else None
    if rdf is None:
        return {"oai_identifier": oai_id}

    # ── ore:Aggregation ───────────────────────────────────────────────────────
    agg = rdf.find("ore:Aggregation", NS)
    image_url = None
    rights_uri = ""
    if agg is not None:
        shown_by = agg.find("edm:isShownBy", NS)
        if shown_by is not None:
            image_url = shown_by.get(f"{{{NS['rdf']}}}resource")
        rights_el = agg.find("edm:rights", NS)
        if rights_el is not None:
            rights_uri = rights_el.get(f"{{{NS['rdf']}}}resource", "")

    # ── edm:ProvidedCHO ───────────────────────────────────────────────────────
    cho = rdf.find("edm:ProvidedCHO", NS)
    accession_number = title = date_display = description = artwork_type = ""
    creator_uri = None
    extent_text = None
    if cho is not None:
        def _t(tag):
            el = cho.find(tag, NS)
            return (el.text or "").strip() if el is not None else ""
        accession_number = _t("dc:identifier")
        title            = _t("dc:title")
        date_display     = _t("dcterms:created")
        description      = _t("dc:description")
        artwork_type     = _t("dc:type")
        creator_el = cho.find("dc:creator", NS)
        if creator_el is not None:
            creator_uri = creator_el.get(f"{{{NS['rdf']}}}resource")
        extent_el = cho.find("dcterms:extent", NS)
        if extent_el is not None:
            extent_text = (extent_el.text or "").strip()

    # ── Resolve artist from edm:Agent ─────────────────────────────────────────
    artist = ""
    if creator_uri:
        for agent in rdf.findall("edm:Agent", NS):
            if agent.get(f"{{{NS['rdf']}}}about") == creator_uri:
                label_el = agent.find("skos:prefLabel", NS)
                if label_el is not None:
                    artist = (label_el.text or "").strip()
                break

    height_cm, width_cm = _parse_dimensions_edm(extent_text)

    return {
        "oai_identifier":   oai_id,
        "accession_number": accession_number,
        "title":            title,
        "artist":           artist,
        "date_display":     date_display,
        "description":      description or None,
        "artwork_type":     artwork_type or None,
        "image_url":        image_url,
        "rights_uri":       rights_uri,
        "is_public_domain": _is_public_domain_rights(rights_uri),
        "height_cm":        height_cm,
        "width_cm":         width_cm,
    }


class RijksArtworkFactory(ArtworkMetadataFactory):
    def __init__(self):
        super().__init__("rijks")

    def create_metadata(self, data: Dict[str, Any]) -> Optional[ArtworkMetadata]:
        obj_num = data.get("objectNumber", "").strip()
        if not obj_num:
            return None

        web_image = data.get("webImage") or {}
        img_url = web_image.get("url", "").strip()
        if not img_url:
            return None
        full_url = img_url + "=s0"

        try:
            dating = data.get("dating") or {}
            year_early = dating.get("yearEarly")
            year_late = dating.get("yearLate")

            dims = data.get("dimensions") or []
            height_cm, width_cm, depth_cm = None, None, None

            return ArtworkMetadata(
                id=obj_num,
                accession_number=obj_num,
                title=data.get("title", "Untitled") or "Untitled",
                artist=data.get("principalOrFirstMaker", "Unknown Artist") or "Unknown Artist",
                date_display=dating.get("presentingDate"),
                date_start=str(year_early) if year_early else None,
                date_end=str(year_late) if year_late else None,
                medium=data.get("physicalMedium"),
                height_cm=height_cm,
                width_cm=width_cm,
                depth_cm=depth_cm,
                is_public_domain=True,
                credit_line=(data.get("acquisition") or {}).get("creditLine"),
                description=data.get("plaqueDescriptionEnglish") or data.get("description"),
                primary_image_url=full_url,
                image_urls={"full": full_url},
                image_pixel_width=web_image.get("width"),
                image_pixel_height=web_image.get("height"),
            )
        except Exception as e:
            self.logger.error(f"Error creating Rijksmuseum metadata for {obj_num}: {e}")
            return None


@dataclass
class RijksProgressState:
    processed_ids: Set[str] = field(default_factory=set)
    success_ids: Set[str] = field(default_factory=set)
    failed_ids: Set[str] = field(default_factory=set)
    error_log: Dict[str, Dict[str, str]] = field(default_factory=dict)
    last_page: int = 1
    total_objects: int = 0


class RijksProgressTracker(BaseProgressTracker):
    def __init__(self, progress_file: Path, max_cache_size: int = 10000, save_batch_size: int = 100):
        self.state = RijksProgressState()
        super().__init__(progress_file, max_cache_size, save_batch_size)
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "rijks")

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "processed_ids": list(self.state.processed_ids),
            "success_ids": list(self.state.success_ids),
            "failed_ids": list(self.state.failed_ids),
            "error_log": self.state.error_log,
            "last_page": self.state.last_page,
            "total_objects": self.state.total_objects,
        }

    def restore_state(self, data: Dict[str, Any]) -> None:
        self.state.processed_ids = set(data.get("processed_ids", []))
        self.state.success_ids = set(data.get("success_ids", []))
        self.state.failed_ids = set(data.get("failed_ids", []))
        self.state.error_log = data.get("error_log", {})
        self.state.last_page = data.get("last_page", 1)
        self.state.total_objects = data.get("total_objects", 0)


class RijksClient(MuseumAPIClient):
    """Rijksmuseum API client — paginated collection endpoint."""

    def __init__(
        self,
        museum_info: MuseumInfo,
        api_key: Optional[str] = None,
        cache_file: Optional[Path] = None,
        progress_tracker: Optional[BaseProgressTracker] = None,
    ):
        self._rijks_api_key = api_key
        if not self._rijks_api_key:
            raise ValueError(
                "Rijksmuseum API key is required. Set RIJKS_API_KEY in your .env file. "
                "Get a free key at https://www.rijksmuseum.nl/en/rijksstudio/"
            )
        super().__init__(museum_info=museum_info, api_key=None, cache_file=cache_file)
        self.progress_tracker = progress_tracker
        self.artwork_factory = RijksArtworkFactory()
        self.logger = setup_logging(settings.logs_dir, settings.log_level, "rijks")

    def _get_auth_header(self) -> str:
        return ""

    def _api_params(self, extra: Dict = None) -> Dict:
        p = {"apikey": self._rijks_api_key, "format": "json"}
        if extra:
            p.update(extra)
        return p

    def get_collection_info(self) -> Dict[str, Any]:
        resp = self.session.get(RIJKS_OAI_URL, params=self._api_params({"ps": 1, "p": 1, "imgonly": "true"}))
        resp.raise_for_status()
        return {"total_objects": resp.json().get("count", 0)}

    def _fetch_detail(self, object_number: str) -> Dict[str, Any]:
        url = f"{RIJKS_OAI_URL}/{object_number}"
        resp = self.session.get(url, params=self._api_params())
        resp.raise_for_status()
        return resp.json().get("artObject") or {}

    def _iter_collection_impl(self, **params) -> Iterator[ArtworkMetadata]:
        page_size = 100
        start_page = 1
        if self.progress_tracker and isinstance(self.progress_tracker, RijksProgressTracker):
            start_page = self.progress_tracker.state.last_page

        page = start_page
        self.logger.info(f"Rijksmuseum: starting at page {page}")

        while True:
            resp = self.session.get(
                RIJKS_OAI_URL,
                params=self._api_params({"ps": page_size, "p": page, "imgonly": "true", "s": "relevance"}),
            )
            resp.raise_for_status()
            body = resp.json()

            if page == start_page:
                total = body.get("count", 0)
                self.logger.info(f"Rijksmuseum: total={total}")
                if self.progress_tracker and isinstance(self.progress_tracker, RijksProgressTracker):
                    self.progress_tracker.state.total_objects = total

            art_objects = body.get("artObjects") or []
            if not art_objects:
                break

            for obj in art_objects:
                obj_num = obj.get("objectNumber", "")
                if self.progress_tracker and self.progress_tracker.is_processed(obj_num):
                    continue

                detail = self._fetch_detail(obj_num)
                merged = {**obj, **detail}
                metadata = self.artwork_factory.create_metadata(merged)
                if metadata:
                    yield metadata

                time.sleep(self.museum_info.rate_limit)

            page += 1
            if self.progress_tracker and isinstance(self.progress_tracker, RijksProgressTracker):
                self.progress_tracker.state.last_page = page
                self.progress_tracker._save_progress()

            self.logger.progress(f"Rijksmuseum: page {page}")
            time.sleep(self.museum_info.rate_limit)

    def _get_artwork_details_impl(self, artwork_id: str) -> Optional[ArtworkMetadata]:
        detail = self._fetch_detail(artwork_id)
        return self.artwork_factory.create_metadata(detail) if detail else None


class RijksImageProcessor(MuseumImageProcessor):
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
            raise RuntimeError(f"Failed to process Rijksmuseum artwork {metadata.id}: {e}")

    def generate_filename(self, metadata: ArtworkMetadata) -> str:
        return sanitize_filename(
            id=f"Rijks_{metadata.id}",
            title=metadata.title,
            artist=metadata.artist,
            max_length=255,
        )
