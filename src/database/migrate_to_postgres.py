"""
Migrate artwork metadata from local SQLite database to a PostgreSQL database.

Usage:
    DATABASE_URL="postgresql://..." uv run python -m src.database.migrate_to_postgres

The DATABASE_URL must be the public Railway URL, e.g.:
    postgresql://postgres:<password>@roundhouse.proxy.rlwy.net:<port>/railway
"""

import argparse
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
    {"code": "smk", "name": "Statens Museum for Kunst"},
    {"code": "rijks", "name": "Rijksmuseum"},
    {"code": "nga", "name": "National Gallery of Art"},
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


def migrate(force_update: bool = False):
    if not SQLITE_PATH.exists():
        print(f"ERROR: SQLite database not found at {SQLITE_PATH}")
        sys.exit(1)

    postgres_url = get_postgres_url()

    print(f"Source : {SQLITE_PATH}")
    print(f"Target : {postgres_url.split('@')[-1]}")  # hide credentials in output
    print()

    # --- engines ---
    src_engine = create_engine(f"sqlite:///{SQLITE_PATH}", echo=False)
    dst_engine = create_engine(
        postgres_url,
        echo=False,
        connect_args={"sslmode": "require"},
    )

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
        mode = "upsert (force-update)" if force_update else "insert-only (skipping existing)"
        print(f"\nMigrating {total:,} artworks in batches of {BATCH_SIZE}  [{mode}]...")

        # Build a src museum_id -> dst museum_id map using code as the key
        src_museums = {m.id: m.code for m in src_session.query(Museum).all()}

        if not force_update:
            # Pre-load all existing (museum_id, original_id) pairs so we can skip them
            # without issuing a SELECT per record.
            print("  Loading existing artwork keys from PostgreSQL...")
            existing_keys: set[tuple[int, str]] = set(
                dst_session.query(Artwork.museum_id, Artwork.original_id).all()
            )
            print(f"  {len(existing_keys):,} records already in PostgreSQL — will skip these.")
        else:
            existing_keys = set()

        inserted = 0
        skipped = 0
        updated = 0
        offset = 0

        def _row_dict(src_artwork: Artwork, dst_museum_id: int) -> dict:
            return dict(
                museum_id=dst_museum_id,
                original_id=src_artwork.original_id,
                accession_number=src_artwork.accession_number,
                title=src_artwork.title,
                artist=src_artwork.artist,
                artist_display=src_artwork.artist_display,
                artist_bio=src_artwork.artist_bio,
                artist_nationality=src_artwork.artist_nationality,
                artist_birth_year=src_artwork.artist_birth_year,
                artist_death_year=src_artwork.artist_death_year,
                date_display=src_artwork.date_display,
                date_start=src_artwork.date_start,
                date_end=src_artwork.date_end,
                medium=src_artwork.medium,
                dimensions=src_artwork.dimensions,
                height_cm=src_artwork.height_cm,
                width_cm=src_artwork.width_cm,
                depth_cm=src_artwork.depth_cm,
                diameter_cm=src_artwork.diameter_cm,
                department=src_artwork.department,
                artwork_type=src_artwork.artwork_type,
                culture=src_artwork.culture,
                style=src_artwork.style,
                is_public_domain=src_artwork.is_public_domain,
                credit_line=src_artwork.credit_line,
                is_on_view=src_artwork.is_on_view,
                is_highlight=src_artwork.is_highlight,
                is_boosted=src_artwork.is_boosted,
                boost_rank=src_artwork.boost_rank,
                has_not_been_viewed_much=src_artwork.has_not_been_viewed_much,
                description=src_artwork.description,
                short_description=src_artwork.short_description,
                provenance=src_artwork.provenance,
                inscriptions=src_artwork.inscriptions,
                fun_fact=src_artwork.fun_fact,
                style_titles=src_artwork.style_titles,
                keywords=src_artwork.keywords,
                primary_image_url=src_artwork.primary_image_url,
                image_urls=src_artwork.image_urls,
                image_path=src_artwork.image_path,
                colorfulness=src_artwork.colorfulness,
                color_h=src_artwork.color_h,
                color_s=src_artwork.color_s,
                color_l=src_artwork.color_l,
                image_pixel_width=src_artwork.image_pixel_width,
                image_pixel_height=src_artwork.image_pixel_height,
                quality_scores=src_artwork.quality_scores,
                quality_score=src_artwork.quality_score,
                created_at=src_artwork.created_at,
                updated_at=src_artwork.updated_at,
            )

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

            to_insert: list[dict] = []
            for src_artwork in batch:
                museum_code = src_museums.get(src_artwork.museum_id)
                if museum_code is None or museum_code not in dst_museums:
                    print(f"  SKIP artwork {src_artwork.id}: unknown museum id {src_artwork.museum_id}")
                    continue

                dst_museum_id = dst_museums[museum_code]
                key = (dst_museum_id, src_artwork.original_id)

                if not force_update and key in existing_keys:
                    skipped += 1
                    continue

                if force_update:
                    existing = (
                        dst_session.query(Artwork)
                        .filter_by(museum_id=dst_museum_id, original_id=src_artwork.original_id)
                        .first()
                    )
                    if existing:
                        for k, v in _row_dict(src_artwork, dst_museum_id).items():
                            setattr(existing, k, v)
                        updated += 1
                        continue

                to_insert.append(_row_dict(src_artwork, dst_museum_id))

            if to_insert:
                dst_session.bulk_insert_mappings(Artwork, to_insert)
                inserted += len(to_insert)

            dst_session.commit()
            offset += BATCH_SIZE
            done = min(offset, total)
            print(
                f"  {done:,}/{total:,} processed  "
                f"(+{inserted} new, ~{updated} updated, {skipped} skipped)",
                end="\r",
            )

        print()
        print(
            f"\nDone. Inserted: {inserted:,}  Updated: {updated:,}  "
            f"Skipped: {skipped:,}  Total processed: {inserted + updated + skipped:,}"
        )

    except Exception as e:
        dst_session.rollback()
        print(f"\nERROR: {e}")
        raise
    finally:
        src_session.close()
        dst_session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate artwork DB from SQLite to PostgreSQL.")
    parser.add_argument(
        "--force-update",
        action="store_true",
        help="Overwrite existing records instead of skipping them (slower).",
    )
    args = parser.parse_args()
    migrate(force_update=args.force_update)
