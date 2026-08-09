# Harvard Art Museums

## 1. Museum name and base URL

- **Name:** Harvard Art Museums (Fogg Museum, Busch-Reisinger Museum, Arthur
  M. Sackler Museum — three collections under one institution)
- **Main site:** https://harvardartmuseums.org
- **API overview page:** https://harvardartmuseums.org/collections/api
- **API base URL:** `https://api.harvardartmuseums.org`
- **API docs (GitHub, authoritative source for this document):**
  https://github.com/harvardartmuseums/api-docs — specifically
  `README.md` and `sections/object.md`, `sections/image.md`,
  `sections/classification.md`.

This is a well-documented, official REST API (unlike LACMA's reverse-engineered
endpoint) — it requires an API key (§7) and is explicitly rate-limited and
restricted to non-commercial use (§9).

## 2. API endpoint for listing public-domain artworks

- **Endpoint:** `GET https://api.harvardartmuseums.org/object`
- **Required param:** `apikey=YOUR_KEY`
- **Params relevant to an open-access image harvest:**
  - `hasimage=1` — only records with at least one image
  - `q=imagepermissionlevel:0` — Elasticsearch-syntax query string filtering
    to the "ok to display images at any size" permission tier (see §5 for why
    this, not `accesslevel`, is the practical open-access filter)
  - `classification`, `century`, `culture`, `person`, `place`, `worktype`,
    `medium`, `period`, `keyword`, `title`, `yearmade`, `color`, `gallery`,
    `exhibition`, `group`, `technique`, `support` — optional facet filters,
    each accepting an ID, a pipe-separated list of IDs, a name/pipe-separated
    list of names, or `"any"`/`"none"` (exact syntax documented per-field in
    `sections/object.md`)
  - `fields` — comma-separated list to restrict the response to only the
    fields needed (reduces payload size for a harvester)
  - `sort` / `sortorder` — sort by field name, or `sort=random` /
    `sort=random:SEED` for randomized order

Example verified-shape request (per docs; requires a real key to execute):

```
https://api.harvardartmuseums.org/object?apikey=YOUR_KEY&hasimage=1&q=imagepermissionlevel:0&size=100&page=1
```

Other documented example queries (from `sections/object.md`):

```
https://api.harvardartmuseums.org/object?q=totalpageviews:0&size=10
https://api.harvardartmuseums.org/object?person=33430
https://api.harvardartmuseums.org/object?classification=Prints&q=totalpageviews:1
https://api.harvardartmuseums.org/object?yearmade=2020-2026
```

A live unauthenticated request (`curl "https://api.harvardartmuseums.org/object?size=1"`)
returned `Unauthorized` — confirming a valid `apikey` is mandatory for every
call, including read-only listing (§7).

## 3. Pagination strategy

- Page-based, via `page` and `size` query params.
- `size`: records per page, **default 10, documented max 100**.
- `page`: page number (1-based).
- Every response includes an `info` block, e.g.:
  ```json
  "info": { "totalrecordsperquery": 10, "totalrecords": 224111, "pages": 22412, "page": 1 }
  ```
  (example shown in docs for an unfiltered `/object` query; per-endpoint
  totals vary — e.g. `/classification/23` shows `objectcount` of 69,081 for
  the "Prints" classification). Iterate `page` from 1 to `info.pages` at
  `size=100` for a full harvest of a given filtered query.
- No cursor/offset mechanism is documented; standard page/size is the only
  strategy. However, each response's `info` block also includes `next` (and
  `prev`, once past page 1) — a fully-formed URL to the next/previous page —
  which can be followed directly instead of manually incrementing `page`;
  its absence signals the end of the result set (confirmed in `README.md`
  §"Paging through data").

## 4. Artwork metadata fields

Full field list per object record, from `sections/object.md`:

| Field | Notes |
|---|---|
| `objectid`, `objectnumber` | internal id / accession number |
| `title`, `titles` | display title / alternate titles |
| `dated`, `datebegin`, `dateend` | display date string + normalized year range |
| `people` | array of associated people (artists, makers) — includes role, name, birthplace/date |
| `culture`, `period`, `century`, `style`, `classification`, `worktype`, `medium`, `technique` | categorical descriptors |
| `dimensions` | free-text |
| `description`, `commentary`, `labeltext` | curatorial text |
| `department`, `division` | e.g. Division of Asian and Mediterranean Art |
| `creditline` | e.g. `"Harvard Art Museums/Arthur M. Sackler Museum, Grace Nichols Strong Memorial Fund"` |
| `provenance` | ownership history text |
| `accessionyear`, `accessionmethod` | acquisition info |
| `primaryimageurl` | canonical single image URL for the object |
| `imagecount`, `images[]` | see §6 for image sub-object fields |
| `colors[]` | programmatically extracted palette: `percent`, `spectrum`, `css3`, `hue` |
| `copyright`, `accesslevel`, `imagepermissionlevel`, `lendingpermissionlevel` | rights/access fields — see §5 |
| `places`, `exhibitions`, `publications`, `videos`, `contextualtext`, `groupings`, `gallery` info, `seeAlso` (IIIF manifest links) | related records |
| `totalpageviews`, `totaluniquepageviews`, `rank` | site engagement metrics |
| `verificationlevel` | 0–4 scale of curatorial research depth |
| `lastupdate`, `createdate` | record timestamps |

For a downloader, the minimal needed set is: `objectid` (id), `title`,
`people[].name` (artist), `primaryimageurl` or `images[].baseimageurl`
(image URL), plus `copyright` / `imagepermissionlevel` for rights filtering.

## 5. Public-domain filter

There is **no single `ispublicdomain` boolean**. Harvard exposes three
separate rights fields on each object (and on each image record) instead:

- **`accesslevel`** — "describes the accessibility of a record": `0` =
  Restricted (record visible only to certain API keys), `1` = Public
  (record visible to all API keys). This gates *record/metadata* visibility,
  not image usage rights.
- **`imagepermissionlevel`** — "describes the level of image copyright
  permissions for a record":
  - `0` = ok to display images at any size
  - `1` = images have restrictions; display at a maximum pixel dimension of 256px
  - `2` = do not display any images
- **`lendingpermissionlevel`** — physical-loan restrictions, not relevant to
  a digital downloader.
- **`copyright`** — free-text string describing the copyright holder when
  the work is not public domain; typically empty/absent for public-domain
  works.

**Practical filter for an open-access harvest:** query with `hasimage=1` and
`q=imagepermissionlevel:0` (full-size display permitted). The docs
explicitly warn: *"URLs for images of objects that have rights restrictions
are excluded for most API users. This means images for many 20th and 21st
century works of art will not be available."* — i.e. Harvard's own API
already omits `baseimageurl`/`primaryimageurl` for restricted images for
most keys, so `imagepermissionlevel:0` plus a non-null `primaryimageurl` is
the strongest available public-domain/open-access signal.

## 6. Image URL format and size parameters

Each entry in an object's `images[]` array (documented in `sections/image.md`):

| Field | Notes |
|---|---|
| `imageid` / `id` | numeric image id |
| `baseimageurl` | "primary internet address for the image delivered via a IIIF image delivery service" — this is the base IIIF Image API URI, not a direct pixel-dump link |
| `iiifbaseuri` | IIIF identifier base for constructing sized requests |
| `renditionnumber` | unique rendition name |
| `width`, `height` | native pixel dimensions |
| `format` | MIME type, typically `image/jpeg` |
| `copyright` | rights holder for this specific image (may differ from the object's `copyright`) |
| `accesslevel` | same 0/1 semantics as the object-level field, scoped to this image |
| `caption`, `alttext`, `description`, `publiccaption` | descriptive text |
| `colors[]` | extracted palette |
| `technique` | photography equipment/software used to capture the image |

**Resizing:** images are served through a IIIF Image API v2.1 endpoint
(spec at http://iiif.io/api/image/2.1/). `README.md` gives a concrete,
verified example: a `baseimageurl` looks like

```
https://nrs.harvard.edu/urn-3:HUAM:799974
```

and a fully-formed request for the full-resolution JPEG is:

```
https://nrs.harvard.edu/urn-3:HUAM:799974/full/full/0/default.jpg
```

i.e. standard IIIF Image API syntax appended directly to `baseimageurl`:

```
{baseimageurl}/full/{width},{height}/0/default.jpg
```

e.g. `{baseimageurl}/full/500,/0/default.jpg` for a 500px-wide JPEG, or
`/full/full/0/default.jpg` for full resolution (this is exactly the pattern
`HARVARDArtworkFactory` in `src/museums/schemas.py` builds from each
`images[].baseimageurl`). Note the docs' own caveat, repeated from §5: image
URLs for rights-restricted objects are excluded for most API keys.

## 7. Authentication requirements

- **Required for every call**, including simple GETs — a live test
  (`curl "https://api.harvardartmuseums.org/object?size=1"`, no key) returned
  `Unauthorized`.
- Obtain a key by submitting the request form at:
  https://docs.google.com/forms/d/1Fe1H4nOhFkrLpaeBpLAnSrIMYvcAxnYWm0IU9a6IkFA/viewform
  (linked from https://harvardartmuseums.org/collections/api). Free, but not
  self-service/instant — it's a manual request form, not an automated
  signup flow.
- Pass the key as `apikey=YOUR_KEY` on every request (query param, not a
  header).

## 8. Approximate collection size

- **Total object records:** ~224,111 (from the documented example `info`
  block: `"totalrecords": 224111"`, `"pages": 22412` at `size=10`). This is
  the whole catalogue, not filtered to public-domain/open-access.
- No documented aggregate count of `imagepermissionlevel:0` records exists
  in the docs; that number can only be obtained by running the filtered
  query (`hasimage=1&q=imagepermissionlevel:0`) once a key is issued and
  reading `info.totalrecords`.
- Per-classification counts are available via `/classification/{id}`
  (`objectcount` field) — e.g. classification 23 ("Prints") has 69,081
  objects — useful for slicing a large harvest the same way LACMA's
  `department` facet was used (see `docs/lacma.md` §3), though Harvard's
  `page`/`size` pagination does not show evidence of LACMA's 10,000-result
  cap in the docs.

## 9. Rate-limiting and terms-of-service constraints

From `README.md` in the docs repo and the API overview page:

- **Rate limit:** "Respect other users of the API by limiting the number of
  API calls to 2500 per day." This is a stated courtesy guideline, not
  confirmed as a hard-enforced server-side cap (no documented HTTP 429
  behavior), but should be treated as authoritative for planning.
- **Non-commercial use only** — explicitly stated restriction on API usage.
- **No long-term caching without permission:** data must not be retained
  longer than two weeks without written permission from Harvard Art Museums.
- **Must use API-provided image URLs, not local copies:** the docs
  instruct integrators to reference the API's own image URLs rather than
  rehosting/mirroring images locally. **This directly conflicts with a
  bulk-download-and-store use case** — building a local ArtServe image
  mirror from this API would go beyond the documented terms and should be
  flagged/confirmed with Harvard (or scoped to caching under two weeks) before
  implementation.
- **Attribution required:** identify content as originating from Harvard
  Art Museums and link back to the object's page on harvardartmuseums.org;
  do not use Harvard's name/logo in a way that implies endorsement or in
  your own hostname without permission.
- **Data freshness:** underlying data refreshes daily (~6am, per the docs
  summary), so records can change day to day.

## Summary for downloader implementation

1. Request an API key via the Google Form linked from
   https://harvardartmuseums.org/collections/api (manual approval, not
   instant — budget lead time before implementation).
2. `GET https://api.harvardartmuseums.org/object?apikey=KEY&hasimage=1&q=imagepermissionlevel:0&size=100&page=N`,
   paginating `page` from 1 through `info.pages`.
3. Take `objectid` (id), `title`, `people[].name` (artist), and
   `primaryimageurl` (or `images[].baseimageurl` + IIIF size suffix) per
   record.
4. Self-throttle to stay under ~2,500 calls/day; at `size=100` that's
   ~250,000 records/day of metadata, so image downloads (not metadata
   pages) will likely be the actual bottleneck.
5. **Before building a persistent local image mirror, confirm this is
   compatible with Harvard's "use API-provided image URLs, do not cache
   beyond two weeks without permission" term** — this is a harder
   constraint than anything seen in the LACMA terms of use.
