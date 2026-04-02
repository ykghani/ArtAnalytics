# MIA Integration Guide

Complete guide for adding Minneapolis Institute of Art (MIA) artworks to your ArtServe collection.

## Overview

The MIA integration uses a **git repository-based approach** instead of a REST API. Metadata is stored in JSON files organized in buckets, and images are fetched from MIA's CDN.

**Key Differences from Other Museums:**
- ✅ No API rate limits (local git repo)
- ✅ Complete metadata available offline
- ✅ Daily updates via `git pull`
- ❌ No real-time API queries
- ❌ Must clone ~500MB repository first

---

## Prerequisites

1. **Git installed** on your system
2. **~1GB free disk space** for the MIA collection repository
3. **ArtServe-Downloader** configured and working
4. **PostgreSQL database** (Railway) accessible
5. **Playwright browsers installed** for web scraping image URLs

---

## Step 1: Clone the MIA Collection Repository

The MIA client will handle git operations automatically, but you can manually clone for testing:

```bash
cd /Users/yusufghani/GitHub/ArtServe/ArtServe-Downloader

# Create MIA data directory
mkdir -p data/mia

# Clone the collection repository (this will take a few minutes)
git clone https://github.com/artsmia/collection.git data/mia/collection

# Verify the structure
ls data/mia/collection/objects | head -5
# Should show: 0/ 1/ 2/ 3/ 4/ ...
```

**Repository Structure:**
```
data/mia/collection/
├── objects/
│   ├── 0/          # Objects 0-999
│   │   ├── 17.json
│   │   ├── 42.json
│   │   └── ...
│   ├── 1/          # Objects 1000-1999
│   │   ├── 1218.json
│   │   └── ...
│   └── ...
├── exhibitions/
└── README.md
```

---

## Step 2: Install Playwright Browsers

MIA image URLs require web scraping because they contain a hash component not in the JSON metadata.

```bash
cd /Users/yusufghani/GitHub/ArtServe/ArtServe-Downloader

# Install Playwright browser (Chromium)
uv run playwright install chromium

# This downloads ~250MB of Chromium browser for headless scraping
```

## Step 3: Configure Environment Variables

Add MIA-specific settings to your `.env` file:

```bash
# In /Users/yusufghani/GitHub/ArtServe/ArtServe-Downloader/.env

# MIA Configuration
MIA_REPO_URL=https://github.com/artsmia/collection.git
MIA_REPO_PATH=mia/collection
MIA_USER_AGENT=MIA-ArtDownloadBot/1.0

# Existing config remains the same...
DEFAULT_CONTACT_EMAIL=your-email@example.com
DOWNLOAD_IMAGES=True
LOG_LEVEL=PROGRESS
```

---

## Step 4: Test MIA Integration (with CDN URL Scraping)

Test that metadata extraction and CDN URL scraping works:

```bash
cd /Users/yusufghani/GitHub/ArtServe/ArtServe-Downloader

# Test with a small limit (5 artworks)
# Note: Each artwork requires ~5-7 seconds for web scraping
uv run python main.py mia --limit 5
```

**Expected Output:**
```
[INFO] Starting download for mia museum
[INFO] Cloning MIA collection repository to data/mia/collection...
[INFO] Repository cloned successfully
[INFO] Processing repository at commit a1b2c3d4
[INFO] Found 150 buckets to process
[INFO] Processing bucket 0 (1/150)
[PROGRESS] Processed 10 artworks...
[SUMMARY] Total processed: 10
[SUMMARY] Successful: 10
[SUMMARY] Failed: 0
```

**Verify in SQLite Database:**
```bash
# Check that metadata was saved
sqlite3 data/artwork.db "SELECT COUNT(*) FROM artworks WHERE museum_code='mia';"
# Should output: 10

# View sample metadata
sqlite3 data/artwork.db "SELECT id, title, artist FROM artworks WHERE museum_code='mia' LIMIT 3;"
```

---

## Step 5: Download with Valid CDN URLs

Now process more artworks (note: web scraping is slower than other museums):

```bash
# Download first 50 artworks with CDN URL scraping
# ~5-7 seconds per artwork = ~4-6 minutes total
uv run python main.py mia --limit 50
```

**What Happens:**
1. ✅ Reads JSON files from git repo
2. ✅ Filters for `image="valid"` and `restricted=0`
3. ✅ Parses metadata (artist, dates, dimensions)
4. ✅ **Scrapes CDN URLs** from `https://collections.artsmia.org/art/{id}` using Playwright
5. ✅ Stores valid CDN URLs like `https://img.artsmia.org/web_objects_cache/.../mia_HASH_full.jpg`
6. ✅ Saves metadata to SQLite `data/artwork.db`
7. ⚠️ **Images NOT downloaded** - only CDN URLs stored (per your requirement)

**Verify CDN URLs Stored:**
```bash
# Check that CDN URLs are valid
sqlite3 data/artwork.db "SELECT id, primary_image_url FROM artworks WHERE museum_code='mia' LIMIT 3;"

# Example output:
# 278|https://img.artsmia.org/web_objects_cache/000000/200/70/278/mia_5014211_full.jpg
# 1218|https://img.artsmia.org/web_objects_cache/001000/200/10/1218/mia_8017891_full.jpg
```

---

## Step 6: Review Downloaded Data

### Check Metadata Quality

```bash
# View detailed metadata for a few artworks
sqlite3 data/artwork.db <<EOF
SELECT
    id,
    title,
    artist,
    artist_nationality,
    artist_birth_year,
    artist_death_year,
    date_display,
    height_cm,
    width_cm,
    is_public_domain,
    is_on_view
FROM artworks
WHERE museum_code='mia'
LIMIT 5;
EOF
```

### Check Progress Tracking

```bash
# View progress state
cat data/mia/cache/processed_ids.json | python3 -m json.tool | head -20
```

**Progress File Structure:**
```json
{
  "processed_ids": ["17", "42", "1218", ...],
  "success_ids": ["17", "42", ...],
  "failed_ids": [],
  "error_log": [],
  "last_bucket": 3,
  "last_object_id": "3456",
  "total_buckets": 150,
  "repo_commit_hash": "a1b2c3d4e5f6..."
}
```

---

## Step 7: Migrate to PostgreSQL (Railway)

Once you're satisfied with local testing, migrate data to your production database.

### Option A: Use ArtServe-Backend Migration Script

```bash
cd /Users/yusufghani/GitHub/ArtServe/ArtServe-Backend

# Sync SQLite data to PostgreSQL
uv run python scripts/sync_artworks.py \
    --source /Users/yusufghani/GitHub/ArtServe/ArtServe-Downloader/data/artwork.db \
    --museum mia \
    --batch-size 1000
```

### Option B: Manual PostgreSQL Import

```bash
# Export from SQLite
sqlite3 data/artwork.db <<EOF
.headers on
.mode csv
.output mia_artworks.csv
SELECT * FROM artworks WHERE museum_code='mia';
.quit
EOF

# Import to PostgreSQL (Railway)
psql $DATABASE_URL -c "\COPY artworks FROM 'mia_artworks.csv' CSV HEADER;"
```

---

## Step 8: Image Storage Strategy

**NO ACTION NEEDED** - MIA artworks already have valid CDN URLs stored in `primary_image_url`.

Your backend and client will fetch images directly from MIA's CDN:
```
https://img.artsmia.org/web_objects_cache/.../mia_HASH_full.jpg
```

This approach:
- ✅ No storage costs for MIA images
- ✅ No bandwidth costs for serving MIA images
- ✅ Images always up-to-date from MIA's CDN
- ⚠️ Depends on MIA's CDN availability (industry standard practice)

---

## Step 9: Full Collection Download

After testing, download the complete MIA collection:

**⚠️ Important Performance Note:**
- Each artwork requires ~5-7 seconds for web scraping
- Full collection: ~30,000 artworks × 6 seconds = **~50 hours**
- Consider running overnight or in batches

```bash
# Remove limit to process all artworks
uv run python main.py mia

# This will:
# - Process all ~150 buckets
# - Scrape CDN URLs for thousands of artworks
# - Take ~2-3 days (due to web scraping overhead)
# - Resume from last checkpoint if interrupted
# - Store CDN URLs (NOT download images)
```

**Monitor Progress:**
```bash
# In another terminal, watch progress
watch -n 5 'sqlite3 data/artwork.db "SELECT COUNT(*) FROM artworks WHERE museum_code=\"mia\";"'
```

**To Pause and Resume:**
- Press `Ctrl+C` to stop
- Run `uv run python main.py mia` again to resume from last checkpoint

---

## Step 10: Update MIA Collection (Daily)

MIA updates their collection daily. To sync new/updated artworks:

```bash
cd /Users/yusufghani/GitHub/ArtServe/ArtServe-Downloader

# Update git repo and download new artworks
uv run python main.py mia

# The client will:
# 1. Run `git pull` to get latest metadata
# 2. Skip already-processed artworks
# 3. Download only new/updated pieces
```

**Automate Daily Updates (Optional):**
```bash
# Add to cron (runs daily at 3 AM)
crontab -e

# Add line:
0 3 * * * cd /Users/yusufghani/GitHub/ArtServe/ArtServe-Downloader && uv run python main.py mia >> data/logs/mia_cron.log 2>&1
```

---

## Troubleshooting

### Git Clone Fails

**Error:** `fatal: unable to access 'https://github.com/artsmia/collection.git/'`

**Solution:**
```bash
# Check git is installed
git --version

# Clone manually to test
git clone https://github.com/artsmia/collection.git /tmp/mia-test

# Check proxy/network settings
git config --global http.proxy
```

### Web Scraping is Slow

**Issue:** Processing MIA artworks takes 5-7 seconds each due to Playwright scraping.

**Solution:**
This is expected behavior. MIA requires web scraping because:
- CDN URLs contain a hash component not in the JSON metadata
- The collection page is client-side rendered (React/Vue)
- Headless browser must wait ~5 seconds for JavaScript to execute

**Optimizations:**
- Run in batches during off-hours
- Use `--limit` flag to process incrementally
- Progress tracker ensures you can resume if interrupted
- Consider parallel processing (advanced - requires multiple browser instances)

### Dimension Parsing Fails

**Issue:** Some artworks have complex dimension strings that don't parse.

**Expected Behavior:** The parser extracts the first `(X x Y cm)` pattern. Complex dimensions (multiple measurements) will only capture the first one. This is acceptable for MVP.

### Progress File Corruption

**Error:** `json.decoder.JSONDecodeError`

**Solution:**
```bash
# Backup and reset progress
mv data/mia/cache/processed_ids.json data/mia/cache/processed_ids.json.bak
uv run python main.py mia --limit 10
```

---

## Data Quality Checks

After downloading, verify data quality:

```sql
-- Check for missing required fields
SELECT COUNT(*) FROM artworks
WHERE museum_code='mia'
AND (title IS NULL OR title = '' OR artist IS NULL OR artist = '');
-- Should be 0 or very low

-- Check public domain filtering
SELECT COUNT(*) FROM artworks
WHERE museum_code='mia'
AND is_public_domain = FALSE;
-- Should be 0 (we only download public domain)

-- Check dimension parsing success rate
SELECT
    COUNT(*) as total,
    COUNT(height_cm) as has_height,
    COUNT(width_cm) as has_width,
    ROUND(100.0 * COUNT(height_cm) / COUNT(*), 2) as height_percent
FROM artworks WHERE museum_code='mia';
-- Expect 60-80% to have parsed dimensions

-- Check image availability
SELECT
    COUNT(*) as total,
    COUNT(primary_image_url) as has_url,
    COUNT(image_pixel_width) as has_dimensions
FROM artworks WHERE museum_code='mia';
-- All should have URLs and dimensions (from metadata)
```

---

## Performance Expectations

Based on the MIA collection characteristics:

| Metric | Expected Value |
|--------|---------------|
| **Total Objects** | ~100,000+ in repository |
| **Public Domain** | ~30-40% (estimated) |
| **With Valid Images** | ~60-70% of public domain |
| **Download Speed** | ~10-20 artworks/minute |
| **Total Time** | 8-12 hours (full collection) |
| **Disk Usage** | 10-15 GB (images) + 500MB (repo) |
| **Database Rows** | ~20,000-30,000 artworks |

---

## Next Steps

After successful MIA integration:

1. ✅ **Update Backend API** to include MIA in museum listings
2. ✅ **Update Client** to show MIA in museum selection
3. ✅ **Test Wallpaper Rendering** with MIA artworks
4. ✅ **Update Documentation** to reflect 4 museum support
5. ✅ **Deploy to Production** with MIA data

---

## Support

**MIA Collection Issues:**
- GitHub: https://github.com/artsmia/collection/issues
- License: CC0 (Public Domain) for metadata
- Images: Separate licensing (check `image_copyright` field)

**ArtServe Integration Issues:**
- Check logs: `data/logs/mia.log`
- Review progress: `data/mia/cache/processed_ids.json`
- Inspect database: `sqlite3 data/artwork.db`

---

## Summary of Changes

### Files Created:
- ✅ `/src/museums/mia.py` - MIA client, factory, progress tracker
- ✅ `MIA_INTEGRATION_GUIDE.md` - This file

### Files Modified:
- ✅ `ArtServe-Shared/python/src/artserve_shared/museums.py` - Added MIA metadata
- ✅ `/src/config.py` - Added MIA configuration
- ✅ `main.py` - Registered MIA museum
- ✅ `.env` - Add MIA environment variables

### Field Mapping Summary:

| MIA Field | ArtworkMetadata Field | Parsing Logic |
|-----------|----------------------|---------------|
| `id` (from URL) | `id` | Extract numeric ID |
| `accession_number` | `accession_number` | Direct |
| `title` | `title` | Direct |
| `artist` | `artist` | Direct |
| `life_date` | `artist_birth_year` / `artist_death_year` | Regex parse "1838 - 1909" |
| `nationality` | `artist_nationality` | Direct |
| `dimension` | `height_cm` / `width_cm` | Regex parse "(X x Y cm)" |
| `restricted` | `is_public_domain` | `0` = True |
| `room` | `is_on_view` | `!= "Not on View"` |
| `country` + `continent` | `keywords` | Append both |
| `image_height` / `image_width` | `image_pixel_height` / `image_pixel_width` | Direct |

**Enjoy your expanded ArtServe collection with MIA! 🎨**
