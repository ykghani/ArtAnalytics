from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, TYPE_CHECKING

from ..config import settings
from .museum_info import MuseumInfo
from ..utils import setup_logging, fetch_remote_image_dimensions

if TYPE_CHECKING:
    from ..config import Settings


def get_settings():
    from ..config import settings

    return settings


@dataclass
class Dimensions:
    """Class for handling artwork dimensions consistently across all museums"""

    height_cm: Optional[float] = None
    width_cm: Optional[float] = None
    depth_cm: Optional[float] = None
    diameter_cm: Optional[float] = None

    @classmethod
    def from_meters(
        cls,
        height: Optional[float] = None,
        width: Optional[float] = None,
        depth: Optional[float] = None,
        diameter: Optional[float] = None,
    ) -> "Dimensions":
        """Convert dimensions from meters to centimeters"""
        return cls(
            height_cm=height * 100 if height is not None else None,
            width_cm=width * 100 if width is not None else None,
            depth_cm=depth * 100 if depth is not None else None,
            diameter_cm=diameter * 100 if diameter is not None else None,
        )

    @classmethod
    def from_cm(
        cls,
        height: Optional[float] = None,
        width: Optional[float] = None,
        depth: Optional[float] = None,
        diameter: Optional[float] = None,
    ) -> "Dimensions":
        """Create dimensions directly from centimeter measurements"""
        return cls(
            height_cm=height, width_cm=width, depth_cm=depth, diameter_cm=diameter
        )


@dataclass
class ArtworkMetadata:
    """Standardized metadata for artwork across different museums"""

    # Core Identifiers
    id: str
    accession_number: str

    # Basic Artwork Info
    title: str
    artist: str
    artist_display: Optional[str] = None
    artist_bio: Optional[str] = None
    artist_nationality: Optional[str] = None
    artist_birth_year: Optional[int] = None
    artist_death_year: Optional[int] = None

    # Dates
    date_display: Optional[str] = None
    date_start: Optional[str] = None
    date_end: Optional[str] = None

    # Physical Details
    medium: Optional[str] = None
    dimensions: Optional[str] = None
    height_cm: Optional[float] = None
    width_cm: Optional[float] = None
    depth_cm: Optional[float] = None
    diameter_cm: Optional[float] = None

    # Classification & Categories
    department: Optional[str] = None
    artwork_type: Optional[str] = None
    culture: Optional[List[str]] = field(default_factory=list)
    style: Optional[str] = None

    # Rights & Display
    is_public_domain: bool = False
    credit_line: Optional[str] = None
    is_on_view: Optional[bool] = None
    is_highlight: Optional[bool] = None
    is_boosted: Optional[bool] = None
    boost_rank: Optional[int] = None
    has_not_been_viewed_much: Optional[bool] = None

    # Rich Content
    description: Optional[str] = None
    short_description: Optional[str] = None
    provenance: Optional[str] = None
    inscriptions: Optional[List[str]] = field(default_factory=list)
    fun_fact: Optional[str] = None
    style_titles: Optional[List[str]] = field(default_factory=list)
    keywords: Optional[List[str]] = field(default_factory=list)

    # Images
    primary_image_url: Optional[str] = None
    image_urls: Optional[Dict[str, str]] = field(default_factory=dict)

    # Analytics Support
    colorfulness: Optional[float] = None
    color_h: Optional[int] = None
    color_s: Optional[int] = None
    color_l: Optional[int] = None

    # Image Quality & Dimensions (captured during download)
    image_pixel_width: Optional[int] = None
    image_pixel_height: Optional[int] = None
    quality_scores: Optional[Dict[str, int]] = None  # Display-specific quality scores
    quality_score: Optional[int] = None  # Backward compatibility: average of all display scores


class ArtworkMetadataFactory(ABC):
    """Abstract base factory for creating ArtworkMetadata objects"""

    def __init__(self, museum_code: str):
        settings = get_settings()
        self.logger = setup_logging(settings.logs_dir, settings.log_level, museum_code)

    @abstractmethod
    def create_metadata(self, data: Dict[str, Any]) -> ArtworkMetadata:
        pass


class AICArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Art Institute of Chicago artwork metadata"""

    def __init__(self):
        super().__init__("aic")

    def create_metadata(self, data: Dict[str, Any]) -> Optional[ArtworkMetadata]:
        # Validate required fields
        if not data or "id" not in data:
            return None

        try:
            # Extract dimensions data safely
            dimensions_detail = data.get("dimensions_detail", [{}])
            dims = dimensions_detail[0] if dimensions_detail else {}
            height = dims.get("height_cm")
            width = dims.get("width_cm")
            depth = dims.get("depth_cm")
            diameter = dims.get("diameter_cm")

            # Extract color data safely
            color_data = data.get("color", {})

            # Process artist display info
            artist_display = data.get("artist_display", "")
            artist_info = (
                artist_display.split("\n")[0] if artist_display else "Unknown Artist"
            )

            # Extract dates
            date_start = data.get("date_start")
            date_end = data.get("date_end")

            # Construct image URLs using IIIF pattern
            image_id = data.get("image_id")
            if image_id is None:
                self.logger.debug(f"Artwork {data.get('id')} has no image data")
                return None

            # Use shared IIIF URL builder (replaces hardcoded URLs)
            from artserve_shared.iiif import build_aic_iiif_urls_legacy

            image_urls = {}
            if image_id:
                image_urls = build_aic_iiif_urls_legacy(image_id)

            return ArtworkMetadata(
                id=str(data["id"]),
                accession_number=data.get("main_reference_number", ""),
                title=data.get("title", "Untitled"),
                artist=data.get("artist_title", artist_info),
                artist_display=artist_display,
                artist_bio=None,  # AIC doesn't provide this
                artist_nationality=None,  # AIC doesn't provide this directly
                artist_birth_year=None,  # Would need to parse from artist_display
                artist_death_year=None,  # Would need to parse from artist_display
                date_display=data.get("date_display", ""),
                date_start=str(date_start) if date_start is not None else None,
                date_end=str(date_end) if date_end is not None else None,
                medium=data.get("medium_display", ""),
                dimensions=data.get("dimensions", ""),
                height_cm=height,
                width_cm=width,
                depth_cm=depth,
                diameter_cm=diameter,
                department=data.get("department_title", ""),
                artwork_type=data.get("artwork_type_title", ""),
                culture=(
                    [data.get("place_of_origin")] if data.get("place_of_origin") else []
                ),
                style=None,
                is_public_domain=bool(data.get("is_public_domain", False)),
                credit_line=data.get("credit_line", ""),
                is_on_view=bool(data.get("is_on_view", False)),
                is_highlight=False,
                is_boosted=bool(data.get("is_boosted", False)),
                boost_rank=data.get("boost_rank"),
                has_not_been_viewed_much=bool(
                    data.get("has_not_been_viewed_much", False)
                ),
                description=data.get("description"),
                short_description=data.get("short_description"),
                provenance=data.get("provenance_text", ""),
                inscriptions=[insc for insc in [data.get("inscriptions")] if insc],
                fun_fact=None,
                style_titles=data.get("style_titles", []),
                keywords=data.get("term_titles", []),
                primary_image_url=image_urls.get("web") if image_urls else None,
                image_urls=image_urls,
                colorfulness=data.get("colorfulness"),
                color_h=color_data.get("h"),
                color_s=color_data.get("s"),
                color_l=color_data.get("l"),
            )
        except Exception as e:
            self.logger.error(
                f"Error creating metadata for artwork {data.get('id', 'unknown')}: {str(e)}"
            )
            return None


class MetArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Metropolitan Museum artwork metadata"""

    def __init__(self):
        super().__init__("met")

    def create_metadata(self, data: Dict[str, Any]) -> ArtworkMetadata:

        if not data:
            self.logger.debug(f"Received empty data")
            return None

        try:
            # Extract measurements
            measurements = data.get("measurements", []) or []
            height = width = depth = diameter = None
            for measure in measurements:
                if "Height" in measure.get("elementMeasurements", {}):
                    height = measure["elementMeasurements"].get("Height")
                if "Width" in measure.get("elementMeasurements", {}):
                    width = measure["elementMeasurements"].get("Width")
                if "Depth" in measure.get("elementMeasurements", {}):
                    depth = measure["elementMeasurements"].get("Depth")
                if "Diameter" in measure.get("elementMeasurements", {}):
                    diameter = measure["elementMeasurements"].get("Diameter")

            artwork = ArtworkMetadata(
                id=str(data["objectID"]),
                accession_number=data.get("accessionNumber", ""),
                title=data.get("title", "Untitled"),
                artist=data.get("artistDisplayName", "Unknown"),
                artist_display=data.get("artistDisplayBio"),
                artist_bio=None,  # Met provides this in artistDisplayBio
                artist_nationality=data.get("artistNationality"),
                artist_birth_year=(
                    int(data["artistBeginDate"])
                    if data.get("artistBeginDate", "").isdigit()
                    else None
                ),
                artist_death_year=(
                    int(data["artistEndDate"])
                    if data.get("artistEndDate", "").isdigit()
                    else None
                ),
                date_display=data.get("objectDate"),
                date_start=(
                    str(data.get("objectBeginDate"))
                    if data.get("objectBeginDate")
                    else None
                ),
                date_end=(
                    str(data.get("objectEndDate"))
                    if data.get("objectEndDate")
                    else None
                ),
                medium=data.get("medium"),
                dimensions=data.get("dimensions"),
                height_cm=height,
                width_cm=width,
                depth_cm=depth,
                diameter_cm=diameter,
                department=data.get("department"),
                artwork_type=data.get("objectName"),
                culture=[data.get("culture")] if data.get("culture") else [],
                style=None,  # Met doesn't provide this directly
                is_public_domain=data.get("isPublicDomain", False),
                credit_line=data.get("creditLine"),
                is_on_view=bool(data.get("GalleryNumber")),
                is_highlight=data.get("isHighlight", False),
                is_boosted=None,  # Met doesn't have this concept
                boost_rank=None,  # Met doesn't have this concept
                has_not_been_viewed_much=None,  # Met doesn't have this concept
                description=None,  # Met doesn't provide this
                short_description=None,  # Met doesn't provide this
                provenance=None,  # Met provides this but not in API
                inscriptions=(
                    [data.get("inscriptions")] if data.get("inscriptions") else []
                ),
                fun_fact=None,  # Met doesn't have this
                style_titles=[],  # Met doesn't provide this
                keywords=[tag.get("term") for tag in data.get("tags", []) or []],
                primary_image_url=data.get("primaryImage"),
                image_urls=(
                    {
                        "primary": data.get("primaryImage"),
                        "small": data.get("primaryImageSmall"),
                    }
                    if data.get("primaryImage")
                    else {}
                ),
                colorfulness=None,  # Met doesn't provide this
                color_h=None,  # Met doesn't provide this
                color_s=None,  # Met doesn't provide this
                color_l=None,  # Met doesn't provide this
            )

            self.logger.artwork(f"Created metadata for artwork {data.get('objectID')}")
            return artwork

        except Exception as e:
            self.logger.error(f"Error creating metadata: {e}")
            return None


class CMAArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Cleveland Museum of Art artwork metadata"""

    def __init__(self):
        super().__init__("cma")

    def create_metadata(self, data: Dict[str, Any]) -> ArtworkMetadata:

        if not data:
            self.logger.debug(f"Received empty data")
            return None

        artwork_id = data.get("id")
        if artwork_id is None:
            return None

        # Handle dimensions
        dimensions_data = data.get("dimensions", {}).get("framed", {})
        height = dimensions_data.get("height")
        width = dimensions_data.get("width")
        depth = dimensions_data.get("depth")

        # Extract creator info
        creators = data.get("creators", [])
        creator = creators[0] if creators else {}

        # Handle images
        images = data.get("images", {})
        image_urls = {}
        for img_type in ["web", "print", "full"]:
            if img_type in images and "url" in images[img_type]:
                image_urls[img_type] = images[img_type]["url"]

        return ArtworkMetadata(
            id=artwork_id,
            accession_number=data.get("accession_number", ""),
            title=data.get("title", "Untitled"),
            artist=creator.get("description", "Unknown"),
            artist_display=creator.get("description"),
            artist_bio=creator.get("biography"),
            artist_nationality=None,  # CMA provides this in biography
            artist_birth_year=(
                int(creator["birth_year"])
                if creator.get("birth_year", "").isdigit()
                else None
            ),
            artist_death_year=(
                int(creator["death_year"])
                if creator.get("death_year", "").isdigit()
                else None
            ),
            date_display=data.get("creation_date"),
            date_start=(
                str(data.get("creation_date_earliest"))
                if data.get("creation_date_earliest")
                else None
            ),
            date_end=(
                str(data.get("creation_date_latest"))
                if data.get("creation_date_latest")
                else None
            ),
            medium=data.get("technique"),
            dimensions=data.get("measurements"),
            height_cm=height,
            width_cm=width,
            depth_cm=depth,
            diameter_cm=None,  # CMA doesn't typically provide this
            department=data.get("department"),
            artwork_type=data.get("type"),
            culture=data.get("culture", []),
            style=None,  # CMA doesn't provide this directly
            is_public_domain=data.get("share_license_status") == "CC0",
            credit_line=data.get("creditline"),
            is_on_view=bool(data.get("current_location")),
            is_highlight=data.get("is_highlight", False),
            is_boosted=None,  # CMA doesn't have this concept
            boost_rank=None,  # CMA doesn't have this concept
            has_not_been_viewed_much=None,  # CMA doesn't have this concept
            description=data.get("description"),
            short_description=data.get("tombstone"),
            provenance="\n".join(
                p.get("description", "") for p in data.get("provenance", [])
            ),
            inscriptions=[i.get("inscription") for i in data.get("inscriptions", [])],
            fun_fact=data.get("did_you_know"),
            style_titles=[],  # CMA doesn't provide this
            keywords=(
                [tag.get("term") for tag in data.get("tags", [])]
                if data.get("tags")
                else []
            ),
            primary_image_url=image_urls.get("web"),
            image_urls=image_urls,
            colorfulness=None,  # CMA doesn't provide this
            color_h=None,  # CMA doesn't provide this
            color_s=None,  # CMA doesn't provide this
            color_l=None,  # CMA doesn't provide this
        )


def _lacma_extract_year(value: Any) -> Optional[int]:
    """Best-effort 4-digit year extraction from a LACMA constituent begin/end date."""
    if value is None:
        return None
    import re

    match = re.search(r"\d{4}", str(value))
    return int(match.group()) if match else None


class LACMAArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating LACMA (Los Angeles County Museum of Art) artwork metadata.

    Built from a single `results[]` entry of the `/api/search` response:
    `{ id, data: { object: {...} } }`. Public-domain status is not echoed on
    the object itself — it is only true because callers only ever fetch with
    the `publicDomain: true` request filter (see docs/lacma.md §5), so it is
    set unconditionally here.
    """

    def __init__(self):
        super().__init__("lacma")

    def create_metadata(self, entry: Dict[str, Any]) -> Optional[ArtworkMetadata]:
        if not entry:
            return None

        artwork_id = entry.get("id")
        obj = (entry.get("data") or {}).get("object") or {}
        if artwork_id is None or not obj:
            return None

        try:
            titles = obj.get("titles") or []
            title = (titles[0].get("title") if titles else None) or "Untitled"

            constituents = obj.get("constituents") or []
            primary_constituent = constituents[0] if constituents else {}
            artist = primary_constituent.get("displayName") or "Unknown"

            images = obj.get("images") or []
            image_urls: Dict[str, str] = {}
            primary_image_url = None
            if images:
                renditions = images[0].get("renditions") or {}
                image_urls = {k: v for k, v in renditions.items() if v}
                # Prefer desktop (jpg, ~900KB) over primary (webp); avoid the
                # multi-hundred-MB archival `access` TIFF unless nothing else exists.
                primary_image_url = (
                    renditions.get("desktop")
                    or renditions.get("primary")
                    or renditions.get("access")
                )

            if not primary_image_url:
                # No usable image — not useful for a downloader.
                return None

            # LACMA's API returns no pixel dimensions (see docs/lacma.md §6), and
            # metadata-only mode never downloads the image itself — so quality
            # scoring needs a lightweight header-only read of the `desktop`
            # rendition (~900KB) rather than the archival TIFF. Best-effort: a
            # failed fetch just leaves dimensions unset rather than failing the
            # whole artwork.
            image_pixel_width: Optional[int] = None
            image_pixel_height: Optional[int] = None
            dims = fetch_remote_image_dimensions(primary_image_url)
            if dims:
                image_pixel_width, image_pixel_height = dims

            raw_culture = obj.get("culture")
            place_made = obj.get("placeMade") or []
            if raw_culture:
                culture = [raw_culture]
            elif place_made:
                culture = list(place_made)
            else:
                culture = []

            return ArtworkMetadata(
                id=str(artwork_id),
                accession_number=obj.get("accessionNumber", ""),
                title=title,
                artist=artist,
                artist_display=primary_constituent.get("displayBio"),
                artist_bio=None,  # LACMA provides this in displayBio
                artist_nationality=primary_constituent.get("nationality"),
                artist_birth_year=_lacma_extract_year(primary_constituent.get("beginDate")),
                artist_death_year=_lacma_extract_year(primary_constituent.get("endDate")),
                date_display=obj.get("dated"),
                medium=obj.get("medium"),
                dimensions=obj.get("dimensions"),
                department=obj.get("department"),
                artwork_type=obj.get("classification"),
                culture=culture,
                is_public_domain=True,
                credit_line=obj.get("creditLine"),
                primary_image_url=primary_image_url,
                image_urls=image_urls,
                image_pixel_width=image_pixel_width,
                image_pixel_height=image_pixel_height,
            )
        except Exception as e:
            self.logger.error(
                f"Error creating metadata for LACMA artwork {artwork_id}: {e}"
            )
            return None


def _harvard_extract_years(displaydate: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """Best-effort birth/death year extraction from a Harvard person's `displaydate`
    string (e.g. "born 1830, died 1900" or "1730-1809"). Returns (birth, death)."""
    if not displaydate:
        return None, None
    import re

    years = re.findall(r"\d{4}", displaydate)
    birth = int(years[0]) if len(years) >= 1 else None
    death = int(years[1]) if len(years) >= 2 else None
    return birth, death


class HARVARDArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating Harvard Art Museums artwork metadata.

    Built from a single `records[]` entry of the `/object` response (see
    docs/harvard.md §4). There is no single public-domain boolean on the
    record — `imagepermissionlevel == 0` ("ok to display images at any
    size") combined with a resolvable image URL is the strongest available
    open-access signal (docs/harvard.md §5), so `is_public_domain` is
    derived from that rather than trusted from a dedicated field.
    """

    def __init__(self):
        super().__init__("harvard")

    def create_metadata(self, data: Dict[str, Any]) -> Optional[ArtworkMetadata]:
        if not data or data.get("objectid") is None:
            return None

        try:
            people = data.get("people") or []
            primary_person = people[0] if people else {}
            artist = primary_person.get("name") or "Unknown"
            artist_birth_year, artist_death_year = _harvard_extract_years(
                primary_person.get("displaydate")
            )

            images = data.get("images") or []
            primary_image_url = data.get("primaryimageurl")
            image_urls: Dict[str, str] = {}
            if primary_image_url:
                image_urls["primary"] = primary_image_url
            for img in images:
                base = img.get("baseimageurl")
                if base and "full" not in image_urls:
                    image_urls["full"] = f"{base}/full/full/0/default.jpg"
            if not primary_image_url:
                primary_image_url = image_urls.get("full")

            if not primary_image_url:
                # No usable image — not useful for a downloader.
                return None

            image_permission_level = data.get("imagepermissionlevel")
            is_public_domain = image_permission_level == 0 and bool(primary_image_url)

            culture = [data.get("culture")] if data.get("culture") else []

            return ArtworkMetadata(
                id=str(data["objectid"]),
                accession_number=data.get("objectnumber", ""),
                title=data.get("title") or "Untitled",
                artist=artist,
                artist_display=primary_person.get("displaydate"),
                artist_nationality=primary_person.get("culture"),
                artist_birth_year=artist_birth_year,
                artist_death_year=artist_death_year,
                date_display=data.get("dated"),
                date_start=(
                    str(data["datebegin"]) if data.get("datebegin") is not None else None
                ),
                date_end=(
                    str(data["dateend"]) if data.get("dateend") is not None else None
                ),
                medium=data.get("medium"),
                dimensions=data.get("dimensions"),
                department=data.get("department"),
                artwork_type=data.get("classification") or data.get("worktype"),
                culture=culture,
                style=data.get("style"),
                is_public_domain=is_public_domain,
                credit_line=data.get("creditline"),
                description=data.get("description") or data.get("commentary"),
                provenance=data.get("provenance"),
                primary_image_url=primary_image_url,
                image_urls=image_urls,
            )
        except Exception as e:
            self.logger.error(
                f"Error creating metadata for Harvard artwork {data.get('objectid', 'unknown')}: {e}"
            )
            return None


# ---------------------------------------------------------------------------
# Getty (J. Paul Getty Museum) — Linked.Art JSON-LD helpers
#
# Object records are `HumanMadeObject` JSON-LD documents (docs/getty.md §4).
# Image availability/rights live on a *separate* `media/image` resource
# referenced from `shows[]` (§6), fetched independently by the client and
# passed in here alongside the object record.
# ---------------------------------------------------------------------------

GETTY_TITLE_PRIMARY = "https://data.getty.edu/local/thesaurus/object-title-primary"
GETTY_TITLE_DISPLAY = "https://data.getty.edu/local/thesaurus/object-title-display"
GETTY_AAT_DIGITAL_IMAGE = "http://vocab.getty.edu/aat/300215302"
GETTY_CC0_URIS = {
    "http://creativecommons.org/publicdomain/zero/1.0/",
    "https://creativecommons.org/publicdomain/zero/1.0/",
}


def _getty_classified_as_ids(node: Dict[str, Any]) -> List[str]:
    return [c.get("id") for c in (node.get("classified_as") or []) if c.get("id")]


def _getty_is_cc0(rights_node: Dict[str, Any]) -> bool:
    return any(cid in GETTY_CC0_URIS for cid in _getty_classified_as_ids(rights_node))


def _getty_metadata_is_cc0(object_data: Dict[str, Any]) -> bool:
    """§5: metadata rights live on the object's own `subject_to[]`."""
    for entry in object_data.get("subject_to") or []:
        if entry.get("_label") == "License for Collection Metadata":
            return _getty_is_cc0(entry)
    return False


def _getty_image_is_cc0(image_data: Optional[Dict[str, Any]]) -> bool:
    """§5: image rights (the actual Open Content flag) live on the separate
    media/image resource's `subject_to[0]`."""
    if not image_data:
        return False
    subject_to = image_data.get("subject_to") or []
    if not subject_to:
        return False
    return _getty_is_cc0(subject_to[0])


def _getty_extract_title(object_data: Dict[str, Any]) -> Optional[str]:
    entries = object_data.get("identified_by") or []
    for target in (GETTY_TITLE_PRIMARY, GETTY_TITLE_DISPLAY):
        for entry in entries:
            if entry.get("type") != "Name":
                continue
            if target in _getty_classified_as_ids(entry) and entry.get("content"):
                return entry["content"]
    for entry in entries:
        if entry.get("type") == "Name" and entry.get("content"):
            return entry["content"]
    return None


def _getty_find_identified_by(object_data: Dict[str, Any], label: str) -> Optional[Dict[str, Any]]:
    for entry in object_data.get("identified_by") or []:
        if entry.get("_label") == label:
            return entry
    return None


def _getty_dimensions(
    dimension_list: Optional[List[Dict[str, Any]]],
) -> tuple[Optional[str], Optional[float], Optional[float], Optional[float]]:
    """Best-effort (display string, height_cm, width_cm, depth_cm) from a
    Linked.Art `dimension[]` list — matched on `_label`/classified_as text
    since docs/getty.md §4 only confirms value/unit fields exist, not exact
    classification terms."""
    height_cm = width_cm = depth_cm = None
    parts = []
    for entry in dimension_list or []:
        value = entry.get("value")
        if value is None:
            continue
        unit = entry.get("unit")
        unit_label = unit.get("_label") if isinstance(unit, dict) else unit
        parts.append(f"{entry.get('_label', '')}: {value} {unit_label or ''}".strip())

        label = (entry.get("_label") or "").lower()
        classified_labels = " ".join(
            (c.get("_label") or "") for c in (entry.get("classified_as") or [])
        ).lower()
        combined = f"{label} {classified_labels}"

        if unit_label and "cm" in str(unit_label).lower():
            try:
                fvalue = float(value)
            except (TypeError, ValueError):
                fvalue = None
            if fvalue is not None:
                if "height" in combined:
                    height_cm = fvalue
                elif "width" in combined:
                    width_cm = fvalue
                elif "depth" in combined:
                    depth_cm = fvalue

    dimensions_str = "; ".join(parts) if parts else None
    return dimensions_str, height_cm, width_cm, depth_cm


def getty_find_image_ref(object_data: Dict[str, Any]) -> Optional[str]:
    """Return the `media/image` resource URL for an object's primary digital
    image (from `shows[]`, §4/§6), or None if it has no image reference.

    Public (no leading underscore) — the client needs this to know what
    second URL to fetch before metadata can be built.
    """
    shows = object_data.get("shows") or []
    for item in shows:
        if item.get("type") == "VisualItem" and GETTY_AAT_DIGITAL_IMAGE in _getty_classified_as_ids(item):
            if item.get("id"):
                return item["id"]
    for item in shows:
        if item.get("type") == "VisualItem" and item.get("id"):
            return item["id"]
    return None


def _getty_access_point_kind(ap: Dict[str, Any]) -> Optional[str]:
    label = (ap.get("_label") or "").lower()
    ids = " ".join(_getty_classified_as_ids(ap)).lower()
    class_labels = " ".join(
        (c.get("_label") or "") for c in (ap.get("classified_as") or [])
    ).lower()
    combined = f"{label} {ids} {class_labels}"
    if "iiif-image" in combined or "iiif image" in combined:
        return "iiif_base"
    if "thumbnail" in combined:
        return "thumbnail"
    if "full-resolution" in combined or "full resolution" in combined:
        return "full_resolution"
    return None


def _getty_extract_access_points(image_data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """§6: `digitally_shown_by[].access_point[]`, classified as one of
    iiif-image (base IIIF service), thumbnail, full-resolution."""
    access_points: Dict[str, str] = {}
    if not image_data:
        return access_points
    for shown_by in image_data.get("digitally_shown_by") or []:
        for ap in shown_by.get("access_point") or []:
            ap_id = ap.get("id")
            if not ap_id:
                continue
            kind = _getty_access_point_kind(ap)
            if kind and kind not in access_points:
                access_points[kind] = ap_id
    return access_points


def _getty_build_image_urls(access_points: Dict[str, str]) -> tuple[Optional[str], Dict[str, str]]:
    image_urls: Dict[str, str] = {}
    iiif_base = access_points.get("iiif_base")
    if iiif_base:
        base = iiif_base.rstrip("/")
        image_urls["full"] = f"{base}/full/max/0/default.jpg"
        image_urls["thumbnail"] = f"{base}/full/!600,600/0/default.jpg"
    if access_points.get("full_resolution"):
        image_urls["full_resolution"] = access_points["full_resolution"]
    if access_points.get("thumbnail") and "thumbnail" not in image_urls:
        image_urls["thumbnail"] = access_points["thumbnail"]

    primary = (
        image_urls.get("full")
        or access_points.get("full_resolution")
        or image_urls.get("thumbnail")
    )
    return primary, image_urls


class GETTYArtworkFactory(ArtworkMetadataFactory):
    """Factory for creating J. Paul Getty Museum artwork metadata.

    Built from a Linked.Art `HumanMadeObject` JSON-LD record (docs/getty.md
    §4) plus, when available, its linked `media/image` resource (§6).
    `is_public_domain` requires CC0 on *both* the metadata rights block and
    the image rights block (§5) — they are independent, and Getty's own docs
    treat the image block as the actual Open Content Program signal.
    """

    def __init__(self):
        super().__init__("getty")

    def create_metadata(
        self, object_data: Dict[str, Any], image_data: Optional[Dict[str, Any]] = None
    ) -> Optional[ArtworkMetadata]:
        if not object_data or not object_data.get("id"):
            return None

        artwork_id = object_data["id"].rstrip("/").split("/")[-1]

        try:
            title = _getty_extract_title(object_data) or "Untitled"

            acc_entry = _getty_find_identified_by(object_data, "Accession Number")
            accession_number = (acc_entry or {}).get("content") or ""

            produced_by = object_data.get("produced_by") or {}
            artist = None
            artist_display = None
            for entry in produced_by.get("referred_to_by") or []:
                label = entry.get("_label")
                if label == "Artist/Maker (Producer) Name" and entry.get("content"):
                    artist = entry["content"]
                elif label == "Artist/Maker (Producer) Description" and entry.get("content"):
                    artist_display = entry["content"]

            dimensions_str, height_cm, width_cm, depth_cm = _getty_dimensions(
                object_data.get("dimension")
            )

            access_points = _getty_extract_access_points(image_data)
            primary_image_url, image_urls = _getty_build_image_urls(access_points)

            if not primary_image_url:
                # No usable image — not useful for a downloader.
                self.logger.debug(f"Getty object {artwork_id} has no usable image")
                return None

            metadata_cc0 = _getty_metadata_is_cc0(object_data)
            image_cc0 = _getty_image_is_cc0(image_data)

            return ArtworkMetadata(
                id=artwork_id,
                accession_number=accession_number,
                title=title,
                artist=artist or "Unknown",
                artist_display=artist_display,
                dimensions=dimensions_str,
                height_cm=height_cm,
                width_cm=width_cm,
                depth_cm=depth_cm,
                is_public_domain=metadata_cc0 and image_cc0,
                credit_line="Courtesy of the J. Paul Getty Museum, Los Angeles",
                primary_image_url=primary_image_url,
                image_urls=image_urls,
            )
        except Exception as e:
            self.logger.error(f"Error creating metadata for Getty object {artwork_id}: {e}")
            return None
