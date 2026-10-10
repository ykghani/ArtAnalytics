"""Attach Jev display-suitability judgments to a batch of artworks before persisting.

Never raises: any failure (missing key, network, SDK error) leaves artworks
unjudged, and the Backend falls back to its keyword heuristic for them.
"""
import asyncio
import logging
from datetime import datetime
from typing import List, Optional

from artserve_shared import display_judgment as dj

from ..database.repository import ArtworkRepository
from ..museums.schemas import ArtworkMetadata

BatchItem = tuple[ArtworkMetadata, str, Optional[str]]


def is_enabled(settings) -> bool:
    return bool(settings.jev_scoring_enabled and settings.typesafe_api_key)


def metadata_to_row(metadata: ArtworkMetadata, museum_code: str) -> dict:
    return {
        "museum_code": museum_code,
        "title": metadata.title,
        "artist": metadata.artist,
        "artist_display": metadata.artist_display,
        "artist_bio": metadata.artist_bio,
        "date_display": metadata.date_display,
        "medium": metadata.medium,
        "artwork_type": metadata.artwork_type,
        "department": metadata.department,
        "dimensions": metadata.dimensions,
        "description": metadata.description,
        "short_description": metadata.short_description,
    }


def score_batch(
    artwork_repo: ArtworkRepository,
    batch: List[BatchItem],
    settings,
    logger: Optional[logging.Logger] = None,
) -> int:
    """Judge unjudged artworks in `batch` in place. Returns how many were judged."""
    log = logger or logging.getLogger(__name__)
    if not batch or not is_enabled(settings):
        return 0
    try:
        already = {}
        for _, museum_code, _ in batch:
            if museum_code not in already:
                ids = [m.id for m, mc, _ in batch if mc == museum_code]
                already[museum_code] = artwork_repo.get_judged_original_ids(museum_code, ids)

        todo = [
            (metadata, museum_code)
            for metadata, museum_code, _ in batch
            if metadata.jev_scored_at is None and metadata.id not in already[museum_code]
        ]
        if not todo:
            return 0

        results = asyncio.run(
            dj.judge_artworks(
                [metadata_to_row(m, code) for m, code in todo],
                api_key=settings.typesafe_api_key,
                model=settings.jev_model,
                concurrency=settings.jev_concurrency,
            )
        )

        now = datetime.utcnow()
        judged = 0
        for (metadata, _), judgment in zip(todo, results):
            if judgment is None:
                continue
            metadata.jev_flat_display_p = judgment.flat_display_p
            metadata.jev_fragment_p = judgment.fragment_p
            metadata.jev_attribution = judgment.attribution
            metadata.jev_appeal = judgment.appeal
            metadata.jev_model = judgment.model
            metadata.jev_scored_at = now
            judged += 1

        log.info(f"Jev scored {judged}/{len(todo)} artworks in batch")
        return judged
    except Exception as e:  # noqa: BLE001 - scoring must never block ingest
        log.warning(f"Jev scoring failed for batch, continuing unjudged: {e}")
        return 0
