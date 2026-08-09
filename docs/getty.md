# Getty Museum (J. Paul Getty Museum) — Open Access Data Source Research

## 1. Museum name and base URL

- **Museum:** J. Paul Getty Museum, Los Angeles, California (part of the J. Paul Getty Trust)
- **Public site:** https://www.getty.edu/
- **Open Content Program info:** https://www.getty.edu/projects/open-content-program/
- **API base URL:** `https://data.getty.edu/museum/collection/`
- **API documentation (JS SPA, see §9 for how it was read):** https://data.getty.edu/museum/collection/docs/
- **Image server base URL:** `https://media.getty.edu/iiif/`

The API is a Linked Data / knowledge graph service based on the [Linked.Art](https://linked.art) standard (a CIDOC-CRM profile), serving JSON-LD. It also exposes a public SPARQL endpoint for graph queries.

## 2. API endpoint for listing public-domain artworks

**There is no REST list/search endpoint.** The official docs state explicitly (verified live, 2026-08-09):

> "We currently don't provide a way to get a list of all of the objects or other entity types in the dataset... it's on our roadmap. We also don't provide a way to download all the data in the dataset; this is also on the roadmap."

Two ways exist to enumerate/discover artwork records instead:

### a) ActivityStream (recommended for a full crawl)
- Root: `GET https://data.getty.edu/museum/collection/activity-stream`
  - Returns an `OrderedCollection` (ActivityStreams 2.0 spec) with `totalItems`, `first`, `last` page links.
  - Verified live: `totalItems: 4547027`, `last` page = `.../activity-stream/page/45521`.
  - Note: `totalItems` counts every `Create`/`Update`/`Delete` activity across **all** entity types (object, person, group, place, document, exhibition, activity), not just artworks — it is not a count of distinct objects.
- Page: `GET https://data.getty.edu/museum/collection/activity-stream/page/{N}` (N = 1..45521 at time of writing)
  - Each page is an `OrderedCollectionPage` with `orderedItems` (an array of `Create`/`Update`/`Delete` activity records), plus `next`/`prev` page links.
  - Each activity item has `type` (`Create`/`Update`/`Delete`), `created`/`endTime` timestamps, and `object.id` / `object.type` pointing at the affected entity (e.g. `type: "HumanMadeObject"` for artworks).
  - Strategy: crawl forward from page 1, collect `object.id` values where `object.type == "HumanMadeObject"`, dedupe by ID (later activities for the same ID supersede earlier ones — a `Delete` means the object should be dropped), then fetch each object record individually (see §4) and filter for public-domain status (§5).

### b) SPARQL endpoint (recommended for discovery/counting/filtering before crawling)
- Endpoint: `POST/GET https://data.getty.edu/museum/collection/sparql` (`Accept: application/sparql-results+json`)
- Web UI: https://data.getty.edu/museum/collection/sparql-ui
- Example query verified live to select public-domain `HumanMadeObject` IDs:
  ```sparql
  PREFIX crm: <http://www.cidoc-crm.org/cidoc-crm/>
  SELECT ?obj WHERE {
    ?obj a crm:E22_Human-Made_Object .
    ?obj crm:P104_is_subject_to ?right .
    ?right crm:P2_has_type <http://creativecommons.org/publicdomain/zero/1.0/> .
    ?obj crm:P65_shows_visual_item ?vi .
  }
  ```
  This can be paged with standard SPARQL `LIMIT`/`OFFSET` to enumerate all matching object IRIs, which are then dereferenced individually as JSON via the REST pattern in §3. This is the practical "list public-domain artworks" endpoint for this API.

### c) Individual record fetch (once you have an ID)
- `GET https://data.getty.edu/museum/collection/object/{ENTITY_ID}` — e.g. `https://data.getty.edu/museum/collection/object/e099dc86-c28e-4ad0-8b02-d7d258caec56` (verified live).

## 3. Pagination strategy

Two different mechanisms depending on which discovery method is used:

- **ActivityStream:** page-number based. URL pattern `.../activity-stream/page/{N}`, starting at 1, with `next`/`prev` links embedded in each page's JSON (`OrderedCollectionPage.next.id`). No page-size parameter — page size is fixed by the server (~100 items/page based on `totalItems / lastPage` ≈ 4,547,027 / 45,521 ≈ 100).
- **SPARQL:** standard `LIMIT n OFFSET m` clauses in the SPARQL query itself.
- There is **no** `page`/`offset`/`cursor` query parameter on the REST entity endpoints themselves — each entity is fetched individually by ID; there is no "list" response to paginate.

## 4. Artwork metadata fields

Object records (`type: "HumanMadeObject"`) are JSON-LD documents in the Linked.Art model. Verified structure (from a live object fetch):

| Concept | JSON path | Notes |
|---|---|---|
| Object ID / URL | `id` | Also serves as the canonical URL: `https://data.getty.edu/museum/collection/object/{uuid}` |
| Title | `identified_by[]` where `classified_as[].id` includes `https://data.getty.edu/local/thesaurus/object-title-primary` (or `-display`) | `identified_by[].content` holds the string; a record commonly has multiple `Name` entries (primary title, display title, alternate titles) — filter by the classification term |
| Accession number | `identified_by[]` where `_label == "Accession Number"` | `.content`, e.g. `"87.AE.31"` |
| Artist / maker | `produced_by.referred_to_by[]` where `_label == "Artist/Maker (Producer) Name"` | `.content` holds a free-text name/attribution string, e.g. `"Attributed to the Hunt Painter (Greek (Lakonian), active 565 - 530 B.C.)"`; there's also a separate `"Artist/Maker (Producer) Description"` entry |
| Image reference(s) | `shows[]` where `type == "VisualItem"` and `classified_as[].id == "http://vocab.getty.edu/aat/300215302"` ("Digital Image") | `id` is a `https://data.getty.edu/media/image/{uuid}` resource — fetch it separately (see §6) to get IIIF access points |
| Dimensions | `dimension[]` | height/width/depth entries with `value`/`unit` |
| Current location | `current_location` | gallery/location entity reference |
| Rights (metadata) | `subject_to[]` where `_label == "License for Collection Metadata"` | see §5 |
| Bibliographic citations | `referred_to_by[]` where `classified_as` includes "Citations (Bibliographic References)" | |

The legacy top-level `representation[]` field (a direct JPEG URL) still appears on records but is **deprecated** as of 2021-03-08 per the docs — the API guidance is to use `shows[]` → the linked `media/image` resource's IIIF `access_point` entries instead (§6).

## 5. Public-domain filter

Two independent rights blocks exist — metadata rights and image rights — and both should be checked:

- **Metadata rights** (does the textual/structured data have restrictions): on the object record, `subject_to[]` contains a `Right` entity (`_label: "License for Collection Metadata"`) whose `classified_as[].id` is checked for `http://creativecommons.org/publicdomain/zero/1.0/`. `identified_by[0].content` on that same node is a human-readable display name (`"Public Domain"`).
- **Image rights** (can the image itself be freely used — this is the Open Content Program flag): fetch the `media/image/{uuid}` resource referenced from `shows[]`; its own `subject_to[0].classified_as[].id` is checked for `http://creativecommons.org/publicdomain/zero/1.0/`. Per the official docs: *"If the value at `subject_to[0].classified_as[0].id` is `https://creativecommons.org/publicdomain/zero/1.0/`, then you're free to use the image without Getty's permission; if the value is anything else, the image is under copyright or some other restrictions apply."*
- SPARQL equivalent (verified live, see §2b): objects where `crm:P104_is_subject_to` points to a `Right` with `crm:P2_has_type <http://creativecommons.org/publicdomain/zero/1.0/>`.
- **Written descriptions / biographies** (object descriptions, artist bios) have their *own separate* rights block at `referred_to_by[].subject_to[0].classified_as[0].id` — often CC BY 4.0 rather than CC0, so attribution text should be pulled from the `subject_of[]` entry classified as `http://vocab.getty.edu/aat/300026687` ("Acknowledgements") if reusing description text.

## 6. Image URL format and size parameters

Images are served via a standard **IIIF Image API** (v2/v3-compatible) server, separate from the metadata API:

- Base pattern: `https://media.getty.edu/iiif/image/{IMAGE_ID}/{region}/{size}/{rotation}/{quality}.{format}`
- Verified live examples:
  - Full resolution: `https://media.getty.edu/iiif/image/{IMAGE_ID}/full/max/0/default.jpg`
  - Thumbnail (max 600×600, aspect preserved): `https://media.getty.edu/iiif/image/{IMAGE_ID}/full/!600,600/0/default.jpg`
  - Width-constrained: `https://media.getty.edu/iiif/image/{IMAGE_ID}/full/!600,/0/default.jpg`
  - Image info/dimensions: `https://media.getty.edu/iiif/image/{IMAGE_ID}/info.json` — returns `width`, `height` (native pixel dimensions) and a `sizes[]` array of pre-computed scaled sizes. Verified example: native `9901×7529`, with `sizes` offering 154×117 up to 4950×3764 in addition to `max`.
- The actual `{IMAGE_ID}` (a different UUID from the object ID) must be obtained from the object's `shows[]` → `media/image/{uuid}` resource's `digitally_shown_by[].access_point[]` list, which has entries classified as `iiif-image` (the base IIIF service), `thumbnail`, and `full-resolution`.
- IIIF Presentation API (manifest with contextual metadata) is also available: `https://media.getty.edu/iiif/manifest/{MANIFEST_ID}`.
- Image responses carry standard CORS headers (`access-control-allow-methods: GET, POST, OPTIONS`) and no rate-limit headers were observed on either the image server or the data API (see §9).

## 7. Authentication requirements

**None.** All endpoints tested (REST object records, ActivityStream, SPARQL, IIIF images) returned data over plain HTTPS with no API key, token, or auth header required. Data is explicitly released under CC0 "without restrictions" per the docs' Usage Guidelines section (two narrow exceptions: some image assets and some written-description text may carry different/attributed licenses — always check the per-record rights block per §5 rather than assuming CC0 blanket-wide).

## 8. Approximate collection size

Verified live via SPARQL COUNT queries against `https://data.getty.edu/museum/collection/sparql` (2026-08-09):

- Total `HumanMadeObject` (artwork) records in the graph: **168,970**
- Records with metadata licensed CC0 (`crm:P104_is_subject_to` → `Right` typed CC0): **119,093**
- Of those, records that also have at least one linked image (`crm:P65_shows_visual_item`): **93,380** — this is the practically-downloadable public-domain-artwork-with-image count.
- The docs page itself states "more than 250,000 objects... as well as objects that have been deaccessioned from the collection" — the discrepancy from the live 168,970 figure is presumably deaccessioned/non-artwork entities excluded by the `E22_Human-Made_Object` filter, or documentation drift; treat the SPARQL-derived figures as authoritative since they're live-measured.
- Independent corroboration: Getty's Open Content Program press materials describe over 160,000 combined public-domain images/archives made available since 2013 program launch.

## 9. Rate limiting / terms of service constraints

- No `Retry-After`, `X-RateLimit-*`, or similar headers were observed on repeated requests to the data API or image server during this research; no documented rate limit was found in the docs text. Treat this as **unconfirmed** rather than "no limit exists" — crawl politely (the ActivityStream alone spans 45,521 pages) and add backoff/retry handling regardless.
- **License:** dataset is CC0 "with some exceptions" (§5). Two documented exceptions:
  1. **Images**: not all linked images are Open Content/CC0 — check each image's own `subject_to` block; contact `rights@getty.edu` to license non-Open-Content images.
  2. **Written descriptions/biographies**: often third-party-copyrighted or CC BY 4.0 (attribution required) rather than CC0 — check `referred_to_by[].subject_to` block per record.
- **No-endorsement clause**: use of the dataset must not imply Getty's endorsement/approval of derived work; Getty trademarks are not part of the dataset license.
- **Derivative datasets**: Getty "asks" (not requires) that derivative datasets built from theirs also be released under CC0.
- **Attribution**: not required for CC0 portions, but Getty requests it; preferred credit line: *"Courtesy of the J. Paul Getty Museum, Los Angeles"*. CC BY 4.0 text (descriptions/bios) does require attribution — the record itself often embeds the exact attribution string to use (see `subject_of[]` classified as AAT term `300026687` "Acknowledgements" in §5).
- **Disclaimer**: data provided "as is" for "exploration, education, experimentation, and fun"; Getty makes no warranties and notes the dataset may contain errors or be incomplete; contact `MuseumCollections@getty.edu` to report issues.
- Contact for questions: `MuseumCollections@getty.edu`. Contact for image licensing outside Open Content: `rights@getty.edu`.
- General copyright policy reference: https://www.getty.edu/legal/copyright.html#collection

---

### Note on how this was researched

`https://data.getty.edu/museum/collection/docs/` is a client-side-rendered (Nuxt) single-page app; a plain HTTP fetch only returns an HTML shell. The actual documentation text was recovered from the page's embedded Nuxt data payload (`https://data.getty.edu/museum/collection/docs/_payload.json`), and all endpoint behaviors, field names, and counts described above were independently verified with live `curl` requests against the REST, ActivityStream, SPARQL, and IIIF endpoints on 2026-08-09.
