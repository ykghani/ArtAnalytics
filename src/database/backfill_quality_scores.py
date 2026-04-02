"""
Backfill quality scores for artworks that have pixel dimensions but no quality scores.

This is needed for museums like MIA where pixel dimensions come from the API metadata
but images are not downloaded (so quality scores were never calculated).

Usage:
    # Backfill all museums
    DATABASE_URL="postgresql://..." uv run python -m src.database.backfill_quality_scores

    # Backfill a specific museum
    DATABASE_URL="postgresql://..." uv run python -m src.database.backfill_quality_scores --museum mia
"""

import os
import sys
import argparse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .models import Artwork, Museum
from artserve_shared.quality_scoring import (
    calculate_quality_scores_for_all_displays,
    calculate_average_quality_score,
)

BATCH_SIZE = 500


def get_postgres_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("ERROR: DATABASE_URL environment variable is not set.")
        sys.exit(1)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def backfill(museum_code: str = None):
    url = get_postgres_url()
    engine = create_engine(url, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Build query: artworks with pixel dims but no quality score
        query = (
            session.query(Artwork)
            .filter(
                Artwork.image_pixel_width.isnot(None),
                Artwork.image_pixel_height.isnot(None),
                Artwork.image_pixel_width > 0,
                Artwork.image_pixel_height > 0,
                Artwork.quality_score.is_(None),
            )
        )

        if museum_code:
            museum = session.query(Museum).filter_by(code=museum_code).first()
            if not museum:
                print(f"ERROR: Museum '{museum_code}' not found in database.")
                sys.exit(1)
            query = query.filter(Artwork.museum_id == museum.id)
            print(f"Backfilling quality scores for: {museum.name}")
        else:
            print("Backfilling quality scores for all museums")

        total = query.count()
        if total == 0:
            print("Nothing to backfill — all artworks already have quality scores.")
            return

        print(f"Found {total:,} artworks to backfill...")

        updated = 0

        while True:
            # Always fetch from offset 0 — committed rows drop out of the WHERE clause
            # so the "next" unprocessed rows naturally bubble up to the top.
            batch = query.order_by(Artwork.id).limit(BATCH_SIZE).all()
            if not batch:
                break

            for artwork in batch:
                scores = calculate_quality_scores_for_all_displays(
                    artwork.image_pixel_width, artwork.image_pixel_height
                )
                artwork.quality_scores = scores
                artwork.quality_score = calculate_average_quality_score(scores)
                updated += 1

            session.commit()
            print(f"  {updated:,}/{total:,} updated", end="\r")

        print()
        print(f"Done. Updated {updated:,} artworks.")

    except Exception as e:
        session.rollback()
        print(f"\nERROR: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill quality scores")
    parser.add_argument(
        "--museum", "-m",
        choices=["aic", "met", "cma", "mia"],
        help="Only backfill a specific museum (default: all)",
    )
    args = parser.parse_args()
    backfill(museum_code=args.museum)
