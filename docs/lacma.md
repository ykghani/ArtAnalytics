# LACMA (Los Angeles County Museum of Art)

## 1. Museum name and base URL

- **Name:** Los Angeles County Museum of Art (LACMA)
- **Main site:** https://www.lacma.org
- **Collections site (search/browse UI + data API):** https://collections.lacma.org
- **Image CDN:** https://collections-images.lacma.org

There is no published/documented public API. `collections.lacma.org` is a Next.js
app (hosted on Netlify) that calls a same-origin JSON endpoint,
`/api/search`, to power its search page. This endpoint was found by
downloading the site's JS bundles (`/_next/static/chunks/*.js`) and locating
the `fetch("/api/search", ...)` call — it is **not documented anywhere** and
should be treated as an internal/unofficial endpoint that could change
without notice. There is no LACMA developer portal, no listed GitHub API
client, and no CMA/Rijksmuseum-style open-access API.

## 2. API endpoint for listing public-domain artworks

- **URL:** `https://collections.lacma.org/api/search`
- **Method:** `POST`
- **Headers:** `Content-Type: application/json` (no auth header required — see §7)
- **Body (JSON):**

```json
{
  "query": "",
  "classification": [],
  "department": [],
  "artist": [],
  "placeMade": [],
  "creditLine": [],
  "building": [],
  "gallery": [],
  "onView": false,
  "hasImage": true,
  "publicDomain": true,
  "sort": "RELEVANCE",
  "page": 1,
  "perPage": 100
}
```

These are exactly the default filter values used client-side (extracted
from bundle `8e1f9eb16de5fb5a.js`), with `hasImage` and `publicDomain` set
to `true` for open-access image harvesting. All array fields (`classification`,
`department`, `artist`, `placeMade`, `creditLine`, `building`, `gallery`)
are facet filters — pass one or more string values to narrow results (values
come from the `facets` block of a prior response, see §5).

Verified live example:

```bash
curl -X POST https://collections.lacma.org/api/search \
  -H "Content-Type: application/json" \
  -d '{"query":"","classification":[],"department":[],"artist":[],"placeMade":[],
       "creditLine":[],"building":[],"gallery":[],"onView":false,"hasImage":true,
       "publicDomain":true,"sort":"RELEVANCE","page":1,"perPage":48}'
```

Returns HTTP 200 with `{"total": ..., "results": [...], "facets": {...}}`.

Sort values (from bundle): `RELEVANCE` (default), `ARTIST_ASC`, `ARTIST_DESC`,
`TITLE_ASC`, `TITLE_DESC`.

## 3. Pagination strategy

- Pagination is page-based via the `page` and `perPage` body fields (not
  offset/cursor). `perPage` defaults to 48 in the UI (options: 48/96/144),
  but the endpoint accepts arbitrary values — values up to 1000 were tested
  and honored (unofficial behavior, not guaranteed to remain uncapped).
- **Hard 10,000-result window per query.** The response's `total` field is
  capped at exactly `10000` for any filter combination that actually matches
  more than 10,000 records (confirmed: an unfiltered `hasImage`/`publicDomain`
  query and a narrow `classification` query both reported `"total": 10000`
  even though the true counts differ). The site's own client code enforces
  this cap on page count:
  `maxPages = Math.min(Math.ceil(total/perPage), Math.ceil(10000/perPage))`
  (from bundle `8e1f9eb16de5fb5a.js`).
- Paging past this window does not error — it silently clamps/repeats the
  last reachable page. Verified: `page=1000` and `page=5000` at `perPage=48`
  returned **identical** result IDs (`[32786, 32793, 32805, ...]`), i.e. the
  offset was clamped rather than continuing to scroll new results.
- **Implication for a full harvest:** since public-domain-with-image works
  total ~25,135 (§8), a single unfiltered query cannot page through them all
  — you must slice the crawl by a facet that keeps each slice under 10,000,
  e.g. iterate `department` (max bucket "Prints and Drawings" = 5,020, well
  under the cap; see facet counts in §8) and paginate each department
  independently with `page`/`perPage`, then dedupe by `id` (a work should
  only have one `department`, so dedup is a safety net, not expected to be
  needed).
- Facet value strings needed for slicing come from the `facets.department[]`
  (`{value, label, count}`) list returned in every search response.

## 4. Artwork metadata fields

Each entry in `results[]` has shape `{ id, data: { object: {...} } }`.
Verified fields under `data.object`:

| Field | Type | Notes |
|---|---|---|
| `id` (top-level) | int | LACMA internal object id, used in image URLs and `/object/{id}` detail page |
| `titles[]` | array | `{title, titleType, displayOrder}`; `titleType` seen: `"Primary Title"`; multiple titles possible |
| `dated` | string | free-text display date, e.g. `"1791"`, `"about 1750-1850"` |
| `department` | string | e.g. `"European Painting and Sculpture"` |
| `classification` | string | e.g. `"Sculpture"`, `"Glass"` |
| `constituents[]` | array | artist/maker records: `{role, firstName, lastName, displayName, displayBio, displayDate, nationality, beginDate, endDate, unmasked, displayOrder}`; `displayName` is the best single "artist" string (e.g. `"Augustin Pajou"`, or `"Unknown"`) |
| `images[]` | array | see §6 for structure |
| `medium` | string | e.g. `"Plaster on painted wood socle and plinth"` |
| `dimensions` | string | free text with metric+imperial |
| `creditLine` | string | e.g. `"Gift of The Ahmanson Foundation"` |
| `accessionNumber` | string | e.g. `"M.75.101"` |
| `placeMade[]` | array of strings | e.g. `["France"]` |
| `culture` | string \| null | often `null` |

There is **no explicit public-domain/rights field** on the object itself
(no `isPublicDomain`, `rights`, or `license` key was present in any sampled
record). Public-domain status is only expressed via the request-side
`publicDomain: true` filter — see §5.

## 5. Public-domain filter

- Set `"publicDomain": true` in the `/api/search` POST body.
- Combine with `"hasImage": true` to restrict to works that actually have
  downloadable images (a public-domain record with no image is not useful
  for a downloader).
- This is a **server-side filter only** — the returned object JSON does not
  echo back a public-domain flag, so downstream code must trust the query
  filter rather than inspecting individual records.
- Corroborating public info: LACMA's own press materials state images
  marked "Public Domain High Resolution Image Available" can be downloaded
  without restriction (see §9), which matches this API filter's intent.

## 6. Image URL format and size parameters

Each `images[]` entry:

```json
{
  "renditions": {
    "access": "https://collections-images.lacma.org/images/{id}/{id}-{n}-print.tif",
    "desktop": "https://collections-images.lacma.org/images/{id}/{id}-{n}-desktop.jpg",
    "primary": "https://collections-images.lacma.org/images/{id}/{id}-{n}-primary.webp",
    "thumbnail": "https://collections-images.lacma.org/images/{id}/{id}-{n}-thumbnail.webp"
  },
  "webCaption": "<p>Artist, <i>Title</i>, date, Los Angeles County Museum of Art, Credit line, photo © Museum Associates/LACMA</p>",
  "displayOrder": 1,
  "copyrightText": ""
}
```

Where `{id}` is the object id and `{n}` is the image's 1-based index for
that object (an object can have multiple images, e.g. alternate views).

Verified live (object 35024, image 1):

| Rendition | Content-Type | Size |
|---|---|---|
| `access` (`-print.tif`) | `image/tiff` | 256,418,896 bytes (~244 MB, full-resolution archival TIFF) |
| `desktop` (`-desktop.jpg`) | `image/jpeg` | 903,353 bytes |
| `primary` (`-primary.webp`) | `image/webp` | 193,496 bytes |
| `thumbnail` (`-thumbnail.webp`) | `image/webp` | 22,834 bytes |

No query-string resizing parameters exist — these four fixed renditions
(`print`/`desktop`/`primary`/`thumbnail`) are the only sizes available; the
filename suffix, not a query param, selects the size. For a downloader,
`desktop` (jpg, ~900 KB) is a reasonable default; `access` (tif) is the
archival/print-quality master but very large.

## 7. Authentication requirements

None observed. `/api/search` and all `collections-images.lacma.org` image
URLs were reachable with plain unauthenticated `curl` requests (no API key,
cookie, or bearer token required). Since the endpoint is undocumented, this
could change without notice — no ToS or docs formally guarantee open access
to `/api/search` itself (see §9).

## 8. Approximate collection size

- LACMA's total encyclopedic collection: **150,000+ objects** (per lacma.org
  press materials).
- Public-domain **with image** objects, measured directly from this API by
  summing facet counts (two independent facets agree):
  - `facets.department[]` counts sum to **25,135**
  - `facets.classification[]` counts sum to **25,135**
  - (Both measured with `hasImage: true, publicDomain: true`, `perPage: 1`,
    reading the `facets` block of the response rather than the capped
    `total` field.)
- Largest department slices for planning a sliced crawl (all safely under
  the 10,000-per-query cap): Prints and Drawings (5,020), Costume and
  Textiles (4,346), Japanese Art (3,078), South and Southeast Asian Art
  (2,259), Art of the Middle East: Ancient (1,459), Art of the Ancient
  Americas (1,387), Decorative Arts and Design (1,257), Egyptian Art
  (1,239), Art of the Middle East: Islamic (1,169), Photography (988),
  Chinese and Korean Art (949), European Painting and Sculpture (766),
  Robert Gore Rifkind Center for German Expressionist Studies (476),
  European Painting and Sculpture: Greek and Roman (330), American Art
  (201), Latin American Art (103), Art of the Pacific (59), Modern Art
  (29), African Art (20).
- Earlier press coverage (LACMA's 2020 "Collections Online" relaunch)
  cited "nearly 20,000" downloadable public-domain images; the live
  facet-sum figure of 25,135 is the more current, directly-measured number
  and should be treated as authoritative for planning.

## 9. Rate limiting / terms of service constraints

- **No rate-limit headers observed.** Five sequential `POST /api/search`
  calls all returned `HTTP 200` in ~0.35–0.55s with no `Retry-After`,
  `X-RateLimit-*`, or similar headers present.
- Responses are served through Netlify's edge/CDN (`server: Netlify`,
  `cache-status: "Netlify Edge"` / `"Next.js"`), so bursts may be cached or
  throttled at the CDN layer under heavy load even without explicit
  API-level limits — no documented number to code against, so a downloader
  should self-throttle conservatively (e.g. a few requests/sec) and back off
  on non-200s.
- **Terms of Use** (https://www.lacma.org/about/contact-us/terms-use):
  - Images labeled "Public Domain High Resolution Image Available" may be
    downloaded and used without restriction, but users are responsible for
    independently verifying they won't infringe third-party rights (privacy/
    publicity); citation including "www.lacma.org" is requested.
  - LACMA does **not** issue a CC0 or other formal open-license grant for
    these images — public-domain status is asserted by LACMA per-object, not
    licensed.
  - All other ("Protected") content requires prior written permission from
    Art Resource, Inc. (LACMA's image-rights representative,
    requests@artres.com), and is restricted to limited non-commercial
    personal use; commercial use is expressly prohibited.
  - The Terms of Use do not mention the `/api/search` endpoint, scraping,
    or automated/programmatic access at all — since the endpoint is
    undocumented and unofficial, a downloader should scope requests to
    `publicDomain: true` results only and avoid aggressive polling.

## Summary for downloader implementation

1. `POST https://collections.lacma.org/api/search` with
   `hasImage: true, publicDomain: true`, sliced by `department` to stay
   under the 10,000-result-per-query window, paginating each department
   with `page`/`perPage` until fewer than `perPage` results are returned.
2. From each result, take `id`, `data.object.titles[0].title`,
   `data.object.constituents[0].displayName` (artist), and
   `data.object.images[].renditions.desktop` (or `.access` for full-res) as
   the image URL.
3. No auth needed; self-throttle since no documented rate limit exists.
4. Treat the endpoint as unofficial/reverse-engineered — no SLA, no
   versioning guarantee.
