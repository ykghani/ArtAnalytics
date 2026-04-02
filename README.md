# ArtServe Museum Collection Downloader

A Python application for downloading and managing artwork metadata and images from major museum APIs, including the Art Institute of Chicago (AIC), Metropolitan Museum of Art (Met), Cleveland Museum of Art (CMA), and Minneapolis Institute of Art (MIA).

## Features

- **Multi-museum support** with standardized data model
- **Parallel downloading** of artwork data and images
- **Resume-capable downloads** with progress tracking
- **SQLite database** for storing artwork metadata locally
- **Rate limiting** and error handling
- **Public domain filtering** - only downloads CC0/public domain works
- **Museum-specific image processing** and filename generation
- **Flexible configuration** via environment variables

## Project Structure

```
ArtServe-Downloader/
├── data/                      # Data storage (git-ignored)
│   ├── artwork.db            # SQLite database with all artwork metadata
│   ├── aic/                  # AIC-specific data
│   │   ├── cache/           # Progress tracking and cache files
│   │   └── images/          # Downloaded artwork images
│   ├── met/                  # Met-specific data
│   │   ├── cache/
│   │   └── images/
│   ├── cma/                  # CMA-specific data
│   │   ├── cache/
│   │   └── images/
│   └── logs/                 # Download logs
│       ├── met_downloader.log
│       ├── aic_downloader.log
│       ├── cma_downloader.log
│       └── artwork_downloader.log
├── src/
│   ├── museums/              # Museum API clients
│   │   ├── aic.py           # Art Institute of Chicago client
│   │   ├── met.py           # Metropolitan Museum client
│   │   ├── cma.py           # Cleveland Museum client
│   │   ├── base.py          # Base museum client
│   │   └── schemas.py       # Data models
│   ├── database/             # Database models and operations
│   │   ├── models.py        # SQLAlchemy models
│   │   ├── database.py      # Database connection
│   │   └── repository.py    # Data access layer
│   ├── download/             # Download management
│   │   ├── artwork_downloader.py
│   │   └── progress_tracker.py
│   ├── config.py             # Configuration settings
│   └── utils.py              # Shared utilities
├── scripts/                   # Utility scripts
├── .env                      # Environment configuration (git-ignored)
├── pyproject.toml            # Project dependencies
└── main.py                   # Main entry point
```

## Requirements

- **Python 3.12+**
- **uv** for dependency management (recommended)

## Setup

### 1. Install uv (if not already installed)

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with Homebrew
brew install uv
```

### 2. Navigate to the downloader directory

```bash
cd ~/GitHub/ArtServe/ArtServe-Downloader
```

### 3. Install dependencies

```bash
# uv will automatically create a virtual environment and install dependencies
uv sync
```

### 4. Configure environment variables

The `.env` file already exists. Key settings:

```bash
# Logging Configuration
LOG_LEVEL=debug              # Options: debug, info, progress, artwork, warning, error

# Download Configuration
BATCH_SIZE=100               # Number of artworks to process before committing to database
RATE_LIMIT_DELAY=1.0        # Seconds between API requests
ERROR_RETRY_DELAY=5.0       # Seconds to wait before retrying failed requests
MAX_RETRIES=5               # Maximum retry attempts for failed downloads

# Common Museum Configuration
DEFAULT_CONTACT_EMAIL=yusuf.k.ghani@gmail.com

# Metropolitan Museum
MET_API_BASE_URL=https://collectionapi.metmuseum.org/public/collection/v1
MET_USER_AGENT=MET-ArtDownloadBot/1.0
MET_RATE_LIMIT=80.0         # Met allows 80 requests/second

# Art Institute of Chicago
AIC_API_BASE_URL=https://api.artic.edu/api/v1/artworks
AIC_USER_AGENT=AIC-ArtDownloadBot/1.0
AIC_RATE_LIMIT=1.0
AIC_USE_DATA_DUMP=true
AIC_DATA_DUMP_PATH=artic-api-data/AIC_json/artworks

# Cleveland Museum of Art
CMA_USE_DATA_DUMP=true
CMA_DATA_DUMP_PATH=CMA_data.json
```

## Usage

### Download from All Museums

```bash
# Download from all configured museums in parallel
uv run python main.py
```

### Download from Specific Museum(s)

```bash
# Download from Met only
uv run python main.py met

# Download from AIC only
uv run python main.py aic

# Download from CMA only
uv run python main.py cma

# Download from multiple museums
uv run python main.py met aic
```

### Available Museum Codes

- `aic` - Art Institute of Chicago
- `met` - Metropolitan Museum of Art
- `cma` - Cleveland Museum of Art
- `mia` - Minneapolis Institute of Art (uses Playwright for CDN URL scraping — requires `playwright install chromium`)

### Monitor Progress

The downloader is **resume-capable** - you can stop and restart downloads without losing progress.

**Check logs in real-time:**

```bash
# Monitor Met downloads
tail -f data/logs/met_downloader.log

# Monitor overall artwork processing
tail -f data/logs/artwork_downloader.log

# Monitor all logs
tail -f data/logs/*.log
```

**Check database contents:**

```bash
# Count artworks by museum
sqlite3 data/artwork.db "
SELECT m.code, m.name, COUNT(a.id) as count
FROM museums m
LEFT JOIN artworks a ON m.id = a.museum_id
GROUP BY m.id;"
```

**Check progress files:**

```bash
# View processed IDs for Met
cat data/met/cache/processed_ids.json | python -m json.tool | head -20

# Check object IDs cache
cat data/met/cache/object_ids_cache.json | python -m json.tool | head -20
```

### Filtering and Query Parameters

The downloader automatically filters for:
- ✅ Public domain artworks only
- ✅ Works with available images
- ✅ Specific departments and artwork types (configurable in `src/config.py`)

**Met Museum Departments** (default):
- `1` - American decorative arts
- `9` - Drawings & prints
- `11` - European prints
- `14` - Islamic art
- `15` - Robert Lehman collection
- `19` - Photographs
- `21` - Modern art

**AIC Departments** (default):
- Modern Art, Contemporary Art, Prints and Drawings, Photography

**CMA Configuration:**
- Uses data dump by default for faster processing
- Filters for CC0 (public domain) works only

**MIA Configuration:**
- Clones/pulls the `artsmia/collection` git repository (~500MB)
- Scrapes CDN image URLs from collection pages using Playwright (~5-7 seconds per artwork)
- Filters for 2D artwork types (Paintings, Prints, Drawings, Photographs, Works on Paper)
- Requires Playwright: `uv run playwright install chromium`

## Data Pipeline

### Current Workflow (Local Development)

```
Museum APIs → Downloader → SQLite (local) → Migration Script → PostgreSQL (Railway)
```

**Step 1: Download metadata and images**
```bash
cd ~/GitHub/ArtServe/ArtServe-Downloader
uv run python main.py met
```
- Fetches artwork metadata from museum APIs
- Downloads images to `data/met/images/`
- Saves metadata to `data/artwork.db` (SQLite)
- Tracks progress in `data/met/cache/processed_ids.json`

**Step 2: Migrate to production database**
```bash
cd ~/GitHub/ArtServe/ArtServe-Backend

# Migration uses DATABASE_URL from backend .env file
uv run python scripts/migrate_from_sqlite.py

# Optional: Limit migration for testing
uv run python scripts/migrate_from_sqlite.py --limit 1000

# Skip confirmation prompt
uv run python scripts/migrate_from_sqlite.py --yes
```

**Migration Features:**
- ✅ Deduplication - skips artworks already in PostgreSQL
- ✅ Batch processing - efficient bulk inserts (1000 per batch)
- ✅ Progress reporting - shows migration status
- ✅ Museum ID remapping - handles different ID schemes

### Data Storage

**Local SQLite (`data/artwork.db`):**
- Stores metadata for all downloaded artworks
- Includes image paths for local files
- Used as source for PostgreSQL migration

**Production PostgreSQL (Railway):**
- Stores metadata only (no image files)
- Uses museum CDN URLs for image delivery
- Powers the API backend

**Images:**
- **Local**: `data/{museum}/images/` (development only)
- **Production**: Served directly from museum CDNs
  - AIC: IIIF image server (dynamic sizing)
  - Met/CMA: Direct URLs from museum APIs

## Error Handling

The downloader handles:

- ✅ **Network timeouts** - automatic retry with exponential backoff
- ✅ **API rate limiting** - respects per-museum rate limits
- ✅ **Missing images** - skips artworks without valid image URLs
- ✅ **Non-public domain works** - automatically filtered
- ✅ **File system errors** - sanitizes filenames, handles long paths
- ✅ **Invalid API responses** - logs errors and continues

**Common Issues:**

1. **Met showing only 24 artworks?**
   - Check `data/logs/met_downloader.log` for errors
   - Many Met artworks lack `primaryImage` URLs
   - Verify public domain filtering isn't too aggressive
   - Check if images are actually being downloaded

2. **Download stops/hangs?**
   - Press Ctrl+C to gracefully stop
   - Progress is saved automatically
   - Resume by running the same command again

3. **Rate limiting errors?**
   - Adjust `RATE_LIMIT_DELAY` in `.env`
   - Met allows 80 req/sec, AIC is more conservative (1 req/sec)

## File Naming Convention

Downloaded files follow the format:
```
{museum_code}_{artwork_id}_{truncated_title}_{artist}.jpg
```

Examples:
```
Met_12345_Starry Night_Vincent van Gogh.jpg
AIC_123456_American Gothic_Grant Wood.jpg
```

## Logging Levels

Configure via `LOG_LEVEL` in `.env`:

- `debug` - Detailed debugging information
- `info` - General informational messages
- `progress` - Progress updates and milestones
- `artwork` - Per-artwork processing logs
- `warning` - Warning messages
- `error` - Error messages only

## Production Considerations

### Current Architecture (MVP)
- ✅ Local downloads to SQLite
- ✅ Manual migration to PostgreSQL
- ✅ Simple, predictable, works for MVP
- ✅ Easy to debug and test

### Future Improvements

**Option 1: Direct Database Write**
- Modify downloader to connect directly to PostgreSQL
- Skip SQLite intermediate step
- Single-step process

**Option 2: Scheduled Background Workers** (Recommended for scale)
- Dockerize the downloader
- Schedule via Railway cron or GitHub Actions
- Automatic daily/weekly syncs
- No manual intervention

**When to upgrade:**
- Adding more museums (5+)
- Artworks exceed 250K
- Need daily updates
- Have budget for scheduled jobs

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License.

## Acknowledgments

- [Art Institute of Chicago API](https://api.artic.edu/docs/)
- [Metropolitan Museum API](https://metmuseum.github.io)
- [Cleveland Museum of Art API](https://www.clevelandart.org/open-access-api)
- [Minneapolis Institute of Art Collection](https://github.com/artsmia/collection)

## Contact

Yusuf Ghani - [yusuf.k.ghani@gmail.com](mailto:yusuf.k.ghani@gmail.com)

Project Link: https://github.com/ykghani/ArtAnalytics
