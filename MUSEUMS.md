# Museum Sources Reference

This document covers all 10 supported museum sources — data types, authentication, rate limits, image handling, and gotchas to know before running a download.

---

## Quick Reference

| Code | Museum | Auth | API Key | Rate Limit | Pagination | ~Images |
|------|--------|------|---------|-----------|-----------|---------|
| `aic` | Art Institute of Chicago | None | No | 1.0 s | Offset | 50K |
| `met` | Metropolitan Museum of Art | None | No | 2.0 s | Object IDs | 490K |
| `cma` | Cleveland Museum of Art | None | No | 80.0 s | Offset | 60K |
| `mia` | Minneapolis Institute of Art | None | No | None | Bucket | 90K |
| `smk` | Statens Museum for Kunst | None | No | 1.0 s | Offset | 50K |
| `nga` | National Gallery of Art | None | No | 5.0 s | CSV (in-memory) | 60K |
| `wellcome` | Wellcome Collection | None | No | 1.0 s | Cursor | 30K |
| `loc` | Library of Congress | None | No | 0.5 s | Page number | 1M+ |
| `rijks` | Rijksmuseum | Query param | **Yes** | 1.0 s | Offset | 360K |
| `tepapa` | Te Papa Tongarewa | Header | **Yes** | 0.2 s | POST offset | 200K |

Rate limits can be overridden via environment variables (e.g. `RIJKS_RATE_LIMIT=2.0`).

---

## Museums Without API Keys

### `aic` — Art Institute of Chicago

- **Data source:** REST API (`https://api.artic.edu/api/v1/artworks`)
- **Image URL:** IIIF — constructed from `image_id`
- **Public domain:** Filtered server-side via `is_public_domain=True` query param
- **Pagination:** Offset-based with configurable page size
- **Optional:** Can use a local JSON data dump instead of live API (`AIC_USE_DATA_DUMP=true`, `AIC_DATA_DUMP_PATH=/path/to/dump`)
- **Gotcha:** Data dump mode skips the live API entirely — useful for bulk ingestion but may lag behind live collection updates

---

### `met` — Metropolitan Museum of Art

- **Data source:** REST API (`https://collectionapi.metmuseum.org/public/collection/v1`)
- **Image URL:** Provided directly in API response
- **Public domain:** Filtered server-side via `isPublicDomain=true`
- **Pagination:** Two-step — fetch filtered object ID list, then fetch each object individually
- **Rate limit:** 2.0 s — slower than most because each object requires a separate request
- **Gotcha:** The ID list fetch can return hundreds of thousands of IDs. The downloader processes them sequentially and checkpoints progress so restarts resume where they left off.

---

### `cma` — Cleveland Museum of Art

- **Data source:** REST API (`https://openaccess-api.clevelandart.org/api`)
- **Image URL:** Provided in API response
- **Public domain:** Filtered via `cc0=null` query param (CC0 works only)
- **Pagination:** Offset-based
- **Optional:** Supports local JSON data dump (`CMA_USE_DATA_DUMP=true`, `CMA_DATA_DUMP_PATH`)
- **Gotcha:** The 80-second rate limit is intentional per the museum's API guidelines — do not lower it without checking current terms. Plan for very long download times (~3–4 days for full collection at default rate).

---

### `mia` — Minneapolis Institute of Art

- **Data source:** Git repository cloned to `data/mia/collection` — JSON files organized in buckets (`objects/{bucket}/{id}.json`)
- **Image URL:** Scraped via Playwright (headless Chromium) from the public collections website. The CDN URL contains a hash component that cannot be derived from the API data alone.
- **Public domain:** Filtered locally — only `image == "valid"` and `restricted == 0`, plus a classification whitelist (paintings, prints, drawings, photographs, etc.)
- **Pagination:** Bucket-based iteration (`bucket = object_id // 1000`)
- **No rate limit:** All I/O is local after the initial clone; scraping has its own browser pacing
- **Gotcha:** Requires Playwright and a Chromium installation. If the collections website changes its HTML structure, image URL extraction will break. The scraper attempts `_full.jpg` first, falls back to `_800.jpg`, then the original URL.
- **Gotcha:** First run clones the full Git repository (~1 GB). Subsequent runs do a `git pull` to update.

---

### `smk` — Statens Museum for Kunst (Denmark)

- **Data source:** REST API (`https://api.smk.dk/api/v1/art/search/`)
- **Image URL:** IIIF preferred (`{image_iiif_id}/full/max/0/default.jpg`), native URL fallback
- **Public domain:** Filtered server-side via `filters=has_image:true,public_domain:true`
- **Pagination:** Offset-based with large page size (2000 items per request — largest of all museums)
- **Language:** API responses are requested in English (`lang=en`)
- **Gotcha:** The IIIF URL field can be null even when a native image URL exists — the fallback handles this. Both URL types are stored in `image_urls`.

---

### `nga` — National Gallery of Art

- **Data source:** Two CSV files from the NGA's public GitHub repository:
  - Objects: `https://raw.githubusercontent.com/NationalGalleryOfArt/opendata/main/data/objects.csv`
  - Images: `https://raw.githubusercontent.com/NationalGalleryOfArt/opendata/main/data/published_images.csv`
- **Image URL:** IIIF — `https://api.nga.gov/iiif/{uuid}/full/!2000,2000/0/default.jpg`
- **Public domain:** All records are public domain by definition (NGA only publishes open-access works in this dataset)
- **Pagination:** None — both CSVs are loaded entirely into memory and joined on `objectid == depictstmsobjectid`, filtered to `viewtype == "primary"`
- **Caching:** CSVs are cached locally under `data/nga/csvs/` to avoid re-downloading on each run
- **Rate limit:** 5.0 s applied between IIIF image downloads (not between metadata records)
- **Gotcha:** The join produces one metadata row per primary image UUID. Artworks without a `uuid` in the images CSV are skipped.
- **Gotcha:** The CSVs are refreshed from GitHub each time caching is invalidated — if NGA updates the files, the local cache must be deleted manually to pick up changes.

---

### `wellcome` — Wellcome Collection

- **Data source:** REST API (`https://api.wellcomecollection.org/catalogue/v2/images`)
- **Image URL:** IIIF — `https://iiif.wellcomecollection.org/image/{id}/full/max/0/default.jpg`
- **Public domain:** Most items are **CC-BY, not CC0/public domain**. Only records with `license_id == "pdm"` or `"cc0"` are marked `is_public_domain=True`. The downloader downloads all images but sets the flag accordingly — check `is_public_domain` before redistribution.
- **Pagination:** Cursor-based — the `nextPage` URL contains a `pageAfter` parameter that is extracted and passed on the next request. The cursor string is persisted so downloads can be resumed.
- **Page size:** 100 items per request
- **Gotcha:** The majority of Wellcome images are freely viewable and downloadable under CC-BY, but are **not** public domain. If your downstream use requires public-domain-only images, filter by `is_public_domain=True` in the database.

---

### `loc` — Library of Congress Prints & Photographs

- **Data source:** REST API (`https://www.loc.gov/pictures/?fo=json`)
- **Image URL:** Prefers IIIF URLs containing `"image-services/iiif"` sorted by highest width; falls back to the largest JPEG available in the `files` list
- **Public domain:** Determined locally by inspecting the `rights_advisory` field for phrases: `"no known restrictions"`, `"no known copyright restrictions"`, or `"public domain"` (case-insensitive). Items that don't match are skipped entirely.
- **Pagination:** Page-based (`sp=N`, `c=100` items per page). Resumes from `last_page` on restart.
- **Page size:** 100 items per request
- **Gotcha:** The `rights_advisory` field can be a string or an array — the factory normalises both forms. Items with ambiguous or absent rights are skipped rather than downloaded.
- **Gotcha:** The LOC collection is enormous (millions of items). A full download at 0.5 s/page will take weeks. Use `--limit` or plan for long-running sessions with regular checkpointing.

---

## Museums That Require API Keys

### `rijks` — Rijksmuseum Amsterdam

- **Data source:** REST API (`https://www.rijksmuseum.nl/api/nl/collection`)
- **Auth:** Free API key from [Rijksstudio](https://www.rijksmuseum.nl/en/rijksstudio/) — set as `RIJKS_API_KEY` in `.env`
- **Auth method:** `apikey` query parameter (not an Authorization header)
- **Image URL:** `{webImage.url}=s0` — the `=s0` suffix is a Google Arts & Culture parameter that requests maximum resolution
- **Public domain:** All results are CC0 — the dataset only includes open-access works
- **Pagination:** Offset-based (`p` = page number, `ps` = page size, default 100). Resumes from `last_page`.
- **Detail fetch:** Each item triggers a second request to `/api/nl/collection/{objectNumber}` to retrieve enriched metadata (dimensions, medium, description). This doubles the request count.
- **Gotcha:** The per-item detail request means the effective rate is one detail fetch per item, not per page. With 100 items per page and 1.0 s rate limit between pages, a full 360K collection takes several days.
- **Gotcha:** If `RIJKS_API_KEY` is not set, the client raises a `ValueError` at startup with instructions for obtaining a key.

---

### `tepapa` — Te Papa Tongarewa (Museum of New Zealand)

- **Data source:** REST API (`https://data.tepapa.govt.nz/collection`)
- **Auth:** Free API key from [Te Papa API docs](https://data.tepapa.govt.nz/docs/) — set as `TEPAPA_API_KEY` in `.env`
- **Auth method:** `x-api-key` request header
- **Request method:** POST (not GET) — the search endpoint requires a JSON body
- **Image URL:** `hasRepresentation[0].media.contentUrl` from the first downloadable representation
- **Public domain:** Two-layer filtering:
  1. Server-side: POST body filter `{"field": "hasRepresentation.rights.allowsDownload", "keyword": "true"}` — only returns items with at least one downloadable image
  2. Client-side: `is_public_domain` flag set based on whether `rightsType.value` contains `"cc0"` or `"public domain"` (case-insensitive)
- **Pagination:** Offset-based via POST body (`from`, `size`). Resumes from `last_from`.
- **Gotcha:** The POST-based pagination is unique in this codebase — all other museums use GET. Do not confuse `get_collection_info()` (also POST) with a read-only probe.
- **Gotcha:** If `TEPAPA_API_KEY` is not set, the `x-api-key` header is simply omitted — the API will return authentication errors at runtime rather than failing fast. Ensure the key is configured before starting a long download.

---

## Environment Variables

Copy `.env.example` and fill in the required keys:

```bash
# Required only for Rijksmuseum
RIJKS_API_KEY=your_key_here

# Required only for Te Papa
TEPAPA_API_KEY=your_key_here

# Optional overrides (all have sensible defaults)
RIJKS_RATE_LIMIT=1.0
TEPAPA_RATE_LIMIT=0.2
NGA_RATE_LIMIT=5.0
WELLCOME_RATE_LIMIT=1.0
LOC_RATE_LIMIT=0.5
SMK_RATE_LIMIT=1.0
MET_RATE_LIMIT=2.0
AIC_RATE_LIMIT=1.0
CMA_RATE_LIMIT=80.0
```

---

## Resuming Downloads

All downloaders checkpoint progress to a JSON file in `data/{museum}/progress/`. On restart, the download picks up where it left off:

| Museum | Resume key |
|--------|-----------|
| `nga` | `last_index` (row index into joined CSV) |
| `wellcome` | `next_cursor` (opaque cursor string) |
| `loc` | `last_page` (1-indexed page number) |
| `rijks` | `last_page` (1-indexed page number) |
| `tepapa` | `last_from` (0-indexed offset) |
| `smk` | `last_offset` (0-indexed offset) |
| `mia` | `last_bucket`, `last_object_id` |
| `aic` | offset-based |
| `met` | last processed object ID |
| `cma` | offset-based |

To force a fresh start, delete the progress file or use `--reset-progress` if supported.
