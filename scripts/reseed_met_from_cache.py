"""
Re-seed Met artworks from cached successful IDs.

This script:
1. Reads successful IDs from progress file (7,651 artworks)
2. Fetches metadata from Met API for each successful ID
3. Saves metadata directly to SQLite database
4. Can then be migrated to PostgreSQL using migrate_from_sqlite.py

Usage:
    cd ~/GitHub/ArtServe/ArtServe-Downloader
    uv run python scripts/reseed_met_from_cache.py

    # Optional: Limit for testing
    uv run python scripts/reseed_met_from_cache.py --limit 100
"""

import sys
import json
import time
from pathlib import Path
from typing import List, Optional
import os

# Add parent directory to path and set working directory to project root
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)  # Ensure .env is found

from src.config import settings
from src.database.database import Database
from src.database.repository import ArtworkRepository
from src.museums.met import MetClient, MetProgressTracker
from src.museums.schemas import MuseumInfo
from src.utils import setup_logging

# Paths
PROGRESS_FILE = Path(__file__).parent.parent / "data" / "met" / "cache" / "processed_ids.json"
DATABASE_PATH = Path(__file__).parent.parent / "data" / "artwork.db"


def load_successful_ids() -> List[str]:
    """Load list of successfully processed Met artwork IDs."""
    with open(PROGRESS_FILE, 'r') as f:
        data = json.load(f)
        success_ids = data.get('success_ids', [])
        print(f"📋 Found {len(success_ids):,} successful Met artwork IDs")
        return success_ids


def fetch_and_save_artworks(
    artwork_ids: List[str],
    limit: Optional[int] = None,
    skip_existing: bool = True
):
    """
    Fetch artwork metadata from Met API and save to database.

    Args:
        artwork_ids: List of Met object IDs to fetch
        limit: Maximum number to fetch (None = all)
        skip_existing: Skip artworks already in database
    """
    # Initialize settings paths
    project_root = Path(__file__).parent.parent
    settings.initialize_paths(project_root)

    logger = setup_logging(settings.logs_dir, settings.log_level, "reseed_met")

    # Initialize database
    db = Database(DATABASE_PATH)
    db.create_tables()
    with db.session_scope() as session:
        db.init_museums(session)

    # Initialize Met client
    museum_info = MuseumInfo(
        name="Metropolitan Museum of Art",
        base_url="https://collectionapi.metmuseum.org/public/collection/v1",
        code="met",
        user_agent="MET-ArtDownloadBot/1.0",
        rate_limit=80.0,
        api_version="v1",
        requires_api_key=False,
    )

    cache_dir = Path(__file__).parent.parent / "data" / "met" / "cache"
    cache_file = cache_dir / "met_cache.sqlite"
    progress_file = cache_dir / "processed_ids.json"

    # Note: We don't pass progress_tracker to avoid updating the original progress file
    client = MetClient(
        museum_info=museum_info,
        api_key=None,
        cache_file=cache_file,
        progress_tracker=None,  # Don't track progress to avoid overwriting
    )

    # Limit artworks if specified
    if limit:
        artwork_ids = artwork_ids[:limit]
        logger.info(f"🔢 Limited to {limit:,} artworks for testing")

    total = len(artwork_ids)
    logger.progress(f"🚀 Starting re-seed of {total:,} Met artworks...")

    # Track stats
    fetched = 0
    saved = 0
    skipped = 0
    failed = 0

    # Rate limiting
    rate_limit_delay = 1.0 / 80.0  # Met allows 80 req/sec

    with db.session_scope() as session:
        artwork_repo = ArtworkRepository(session)

        for idx, object_id in enumerate(artwork_ids, 1):
            # Progress reporting
            if idx % 100 == 0:
                progress_pct = (idx / total) * 100
                logger.progress(
                    f"📊 Progress: {idx:,}/{total:,} ({progress_pct:.1f}%) | "
                    f"Saved: {saved:,} | Skipped: {skipped:,} | Failed: {failed:,}"
                )

            try:
                # Check if already exists
                if skip_existing:
                    existing = artwork_repo.get_artwork("met", object_id)
                    if existing:
                        skipped += 1
                        continue

                # Fetch metadata from Met API
                artwork_metadata = client._get_artwork_details_impl(object_id)

                if artwork_metadata:
                    fetched += 1

                    # Save to database (without image path)
                    artwork_repo.create_or_update_artwork(
                        metadata=artwork_metadata,
                        museum_code="met",
                        image_path=None,  # Don't store local image path
                    )
                    saved += 1

                    logger.artwork(
                        f"✓ Saved: {artwork_metadata.title} by {artwork_metadata.artist}"
                    )
                else:
                    logger.warning(f"⚠️  No metadata returned for ID {object_id}")
                    failed += 1

                # Rate limiting
                time.sleep(rate_limit_delay)

            except Exception as e:
                logger.error(f"❌ Error fetching ID {object_id}: {e}")
                failed += 1
                continue

    # Final summary
    print()
    print("=" * 60)
    print("✅ Re-seed Complete!")
    print("=" * 60)
    print(f"Total IDs processed: {total:,}")
    print(f"  ✓ Fetched from API: {fetched:,}")
    print(f"  ✓ Saved to database: {saved:,}")
    print(f"  ⊘ Skipped (already existed): {skipped:,}")
    print(f"  ✗ Failed: {failed:,}")
    print()

    # Verify database count
    from sqlalchemy import text
    with db.session_scope() as session:
        artwork_repo = ArtworkRepository(session)
        met_count = session.execute(
            text("SELECT COUNT(*) FROM artworks WHERE museum_id = (SELECT id FROM museums WHERE code = 'met')")
        ).scalar()
        print(f"📊 Total Met artworks in database: {met_count:,}")

    print()
    print("Next steps:")
    print("  1. Verify data in SQLite:")
    print(f"     sqlite3 {DATABASE_PATH}")
    print("  2. Migrate to PostgreSQL:")
    print("     cd ~/GitHub/ArtServe/ArtServe-Backend")
    print("     uv run python scripts/migrate_from_sqlite.py --yes")
    print()


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Re-seed Met artworks from cached successful IDs"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of artworks to fetch (for testing)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch even if artwork already exists in database"
    )

    args = parser.parse_args()

    # Validate progress file exists
    if not PROGRESS_FILE.exists():
        print(f"❌ Progress file not found: {PROGRESS_FILE}")
        sys.exit(1)

    # Load successful IDs
    success_ids = load_successful_ids()

    if not success_ids:
        print("❌ No successful IDs found in progress file")
        sys.exit(1)

    # Confirm before proceeding
    if args.limit:
        print(f"⚠️  Test mode: Will fetch {args.limit:,} artworks only")
    else:
        print(f"📦 Will fetch {len(success_ids):,} artworks from Met API")

    print(f"💾 Database: {DATABASE_PATH}")
    print(f"🔄 Skip existing: {not args.force}")
    print()

    response = input("Continue? [y/N]: ")
    if response.lower() != 'y':
        print("Cancelled.")
        sys.exit(0)

    # Fetch and save
    fetch_and_save_artworks(
        artwork_ids=success_ids,
        limit=args.limit,
        skip_existing=not args.force
    )


if __name__ == "__main__":
    main()
