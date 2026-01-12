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

    # Initialize Met client with more browser-like User-Agent
    museum_info = MuseumInfo(
        name="Metropolitan Museum of Art",
        base_url="https://collectionapi.metmuseum.org/public/collection/v1",
        code="met",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        rate_limit=1.0,  # Conservative 1 req/sec to avoid triggering WAF
        api_version="v1",
        requires_api_key=False,
    )

    # Note: We don't pass progress_tracker or cache_file to get fresh requests
    # and avoid any caching issues that might trigger 403s
    client = MetClient(
        museum_info=museum_info,
        api_key=None,
        cache_file=None,  # Disable caching to avoid 403 issues
        progress_tracker=None,  # Don't track progress to avoid overwriting
    )

    # Add additional headers to make requests look more legitimate
    client.session.headers.update({
        "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })

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
    consecutive_403_errors = 0

    # Rate limiting - Start conservative to avoid triggering WAF
    base_rate_limit_delay = 1.0  # Start at 1 req/sec instead of 80
    current_delay = base_rate_limit_delay

    with db.session_scope() as session:
        artwork_repo = ArtworkRepository(session)

        for idx, object_id in enumerate(artwork_ids, 1):
            # Progress reporting
            if idx % 100 == 0:
                progress_pct = (idx / total) * 100
                logger.progress(
                    f"📊 Progress: {idx:,}/{total:,} ({progress_pct:.1f}%) | "
                    f"Saved: {saved:,} | Skipped: {skipped:,} | Failed: {failed:,} | "
                    f"Delay: {current_delay:.2f}s"
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

                    # Reset consecutive 403 counter and gradually speed up
                    consecutive_403_errors = 0
                    current_delay = max(base_rate_limit_delay, current_delay * 0.95)
                else:
                    logger.warning(f"⚠️  No metadata returned for ID {object_id}")
                    failed += 1

                # Rate limiting with jitter
                import random
                jitter = random.uniform(0.8, 1.2)
                time.sleep(current_delay * jitter)

            except Exception as e:
                error_msg = str(e)

                # Check if 403 Forbidden error
                if "403" in error_msg or "Forbidden" in error_msg:
                    consecutive_403_errors += 1
                    logger.error(
                        f"❌ 403 Error for ID {object_id} "
                        f"(consecutive: {consecutive_403_errors}): {e}"
                    )

                    # Exponential backoff for 403 errors
                    if consecutive_403_errors >= 3:
                        backoff_delay = min(300, 2 ** (consecutive_403_errors - 2))
                        logger.warning(
                            f"⚠️  Multiple 403 errors detected. "
                            f"Backing off for {backoff_delay}s..."
                        )
                        time.sleep(backoff_delay)
                        current_delay = min(10.0, current_delay * 2)  # Slow down
                        consecutive_403_errors = 0  # Reset after backoff
                else:
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
