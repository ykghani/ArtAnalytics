"""
Migrate artwork metadata from local SQLite database to a PostgreSQL database.

Usage:
    DATABASE_URL="postgresql://..." uv run python -m src.database.migrate_to_postgres

The DATABASE_URL must be the public Railway URL, e.g.:
    postgresql://postgres:<password>@roundhouse.proxy.rlwy.net:<port>/railway
"""

import os
import sys
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .models import Base, Museum, Artwork

BATCH_SIZE = 500

SQLITE_PATH = Path(__file__).resolve().parents[2] / "data" / "artwork.db"

ALL_MUSEUMS = [
    {"code": "met", "name": "Metropolitan Museum of Art"},
    {"code": "aic", "name": "Art Institute of Chicago"},
    {"code": "cma", "name": "Cleveland Museum of Art"},
    {"code": "mia", "name": "Minneapolis Institute of Art"},
]


def get_postgres_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("ERROR: DATABASE_URL environment variable is not set.")
        print("  Export it before running, e.g.:")
        print('  DATABASE_URL="postgresql://..." uv run python -m src.database.migrate_to_postgres')
        sys.exit(1)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def migrate():
    if not SQLITE_PATH.exists():
        print(f"ERROR: SQLite database not found at {SQLITE_PATH}")
        sys.exit(1)

    postgres_url = get_postgres_url()

    print(f"Source : {SQLITE_PATH}")
    print(f"Target : {postgres_url.split('@')[-1]}")  # hide credentials in output
    print()

    # --- engines ---
    src_engine = create_engine(f"sqlite:///{SQLITE_PATH}", echo=False)
    dst_engine = create_engine(postgres_url, echo=False)

    SrcSession = sessionmaker(bind=src_engine)
    DstSession = sessionmaker(bind=dst_engine)

    # --- create schema on postgres ---
    print("Creating tables on PostgreSQL (if not exist)...")
    Base.metadata.create_all(dst_engine)

    src_session = SrcSession()
    dst_session = DstSession()

    try:
        # --- seed museums ---
        # Use ON CONFLICT DO NOTHING so the insert is safe regardless of sequence state,
        # then reset the sequence to avoid future conflicts.
        print("Seeding museums...")
        for museum_data in ALL_MUSEUMS:
            dst_session.execute(
                text(
                    "INSERT INTO museums (name, code) VALUES (:name, :code) "
                    "ON CONFLICT (code) DO NOTHING"
                ),
                museum_data,
            )
            print(f"  Seeded: {museum_data['name']}")
        dst_session.commit()

        # Reset the sequence so future inserts don't collide with existing rows
        dst_session.execute(
            text("SELECT setval('museums_id_seq', (SELECT MAX(id) FROM museums))")
        )
        dst_session.commit()

        # Build a code -> id map for the destination museums
        dst_museums = {m.code: m.id for m in dst_session.query(Museum).all()}

        # --- migrate artworks ---
        total = src_session.query(Artwork).count()
        print(f"\nMigrating {total:,} artworks in batches of {BATCH_SIZE}...")

        inserted = 0
        updated = 0
        offset = 0

        while True:
            batch = (
                src_session.query(Artwork)
                .order_by(Artwork.id)
                .offset(offset)
                .limit(BATCH_SIZE)
                .all()
            )
            if not batch:
                break

            for src_artwork in batch:
                # Remap museum_id to the destination DB's museum id
                src_museum = src_session.query(Museum).get(src_artwork.museum_id)
                if src_museum is None or src_museum.code not in dst_museums:
                    print(f"  SKIP artwork {src_artwork.id}: unknown museum id {src_artwork.museum_id}")
                    continue

                dst_museum_id = dst_museums[src_museum.code]

                existing = (
                    dst_session.query(Artwork)
                    .filter_by(museum_id=dst_museum_id, original_id=src_artwork.original_id)
                    .first()
                )

                if existing:
                    target = existing
                    updated += 1
                else:
                    target = Artwork(museum_id=dst_museum_id, original_id=src_artwork.original_id)
                    dst_session.add(target)
                    inserted += 1

                # Copy every column except the primary key and museum_id/original_id
                target.accession_number = src_artwork.accession_number
                target.title = src_artwork.title
                target.artist = src_artwork.artist
                target.artist_display = src_artwork.artist_display
                target.artist_bio = src_artwork.artist_bio
                target.artist_nationality = src_artwork.artist_nationality
                target.artist_birth_year = src_artwork.artist_birth_year
                target.artist_death_year = src_artwork.artist_death_year
                target.date_display = src_artwork.date_display
                target.date_start = src_artwork.date_start
                target.date_end = src_artwork.date_end
                target.medium = src_artwork.medium
                target.dimensions = src_artwork.dimensions
                target.height_cm = src_artwork.height_cm
                target.width_cm = src_artwork.width_cm
                target.depth_cm = src_artwork.depth_cm
                target.diameter_cm = src_artwork.diameter_cm
                target.department = src_artwork.department
                target.artwork_type = src_artwork.artwork_type
                target.culture = src_artwork.culture
                target.style = src_artwork.style
                target.is_public_domain = src_artwork.is_public_domain
                target.credit_line = src_artwork.credit_line
                target.is_on_view = src_artwork.is_on_view
                target.is_highlight = src_artwork.is_highlight
                target.is_boosted = src_artwork.is_boosted
                target.boost_rank = src_artwork.boost_rank
                target.has_not_been_viewed_much = src_artwork.has_not_been_viewed_much
                target.description = src_artwork.description
                target.short_description = src_artwork.short_description
                target.provenance = src_artwork.provenance
                target.inscriptions = src_artwork.inscriptions
                target.fun_fact = src_artwork.fun_fact
                target.style_titles = src_artwork.style_titles
                target.keywords = src_artwork.keywords
                target.primary_image_url = src_artwork.primary_image_url
                target.image_urls = src_artwork.image_urls
                target.image_path = src_artwork.image_path
                target.colorfulness = src_artwork.colorfulness
                target.color_h = src_artwork.color_h
                target.color_s = src_artwork.color_s
                target.color_l = src_artwork.color_l
                target.image_pixel_width = src_artwork.image_pixel_width
                target.image_pixel_height = src_artwork.image_pixel_height
                target.quality_scores = src_artwork.quality_scores
                target.quality_score = src_artwork.quality_score
                target.created_at = src_artwork.created_at
                target.updated_at = src_artwork.updated_at

            dst_session.commit()
            offset += BATCH_SIZE
            done = min(offset, total)
            print(f"  {done:,}/{total:,} processed  (+{inserted} new, ~{updated} updated)", end="\r")

        print()
        print(f"\nDone. Inserted: {inserted:,}  Updated: {updated:,}  Total: {inserted + updated:,}")

    except Exception as e:
        dst_session.rollback()
        print(f"\nERROR: {e}")
        raise
    finally:
        src_session.close()
        dst_session.close()


if __name__ == "__main__":
    migrate()
