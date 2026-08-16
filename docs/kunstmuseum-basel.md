# Kunstmuseum Basel — Research

Written by hand (not the RESEARCH agent) after three automated RESEARCH attempts
exhausted their turn budget without producing this doc. Root cause: this site has
no documented REST API — it's a Next.js SSR app, and the RESEARCH agent's only
tools (WebSearch/WebFetch) can't see the JSON embedded in the raw HTML, since
WebFetch converts pages to markdown and drops embedded `<script>` JSON. Everything
below was found with plain `curl` against the raw HTML.

## 1. Museum name and base URL

Kunstmuseum Basel (public collection database, separate host from the main
museum site). Base URL: `https://sammlung.kunstmuseumbasel.ch`
(`sammlungonline.kunstmuseumbasel.ch`, the URL shown in most search results and
third-party listings, 301-redirects here).

## 2. API endpoint — no documented REST API; SSR HTML with embedded JSON instead

There is no public REST/JSON API. The frontend is a Next.js app whose object
detail pages are server-rendered with the full record embedded as JSON in a
`<script id="__NEXT_DATA__" type="application/json">` tag — readable with a
plain `curl` + JSON parse, no headless browser needed:

```
GET https://sammlung.kunstmuseumbasel.ch/{lng}/collection/item/{id}
```

`{lng}` = `de` or `en`. `{id}` is the internal numeric object ID (opaque, not
the human-readable inventory number). Confirmed working for `id=1366`
(Donald Judd, "Untitled"), `id=1375`, `id=85838` (Helen Frankenthaler). Invalid
IDs return a clean `404` with `"pageKey":"404"` in the same `__NEXT_DATA__`
structure — cheap and easy to detect.

Backend, for context: the page's runtime config reveals the real system is a
Zetcom MuseumPlus instance at `mp.kumu.swiss/ria-ws/application/module/...`.
Hitting that directly returns `403 Invalid credentials!` — it's called
server-side during SSR with credentials that never reach the browser, so it is
**not** a usable public endpoint. Use the SSR HTML route above instead.

Parse pattern (Python):
```python
import re, json
m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
item = json.loads(m.group(1))["props"]["pageProps"]["data"]["item"]
```

## 3. Pagination strategy — UNRESOLVED, needs a pragmatic fallback

This is the one open gap. The listing/search page (`/{lng}/collection`) is
**not** server-rendered — its `pageProps.data` is empty in the raw HTML, so the
result list is fetched by a client-side XHR call after the page loads. I could
not identify that endpoint by reading the shipped JS bundles (the app-specific
logic lives in dynamically-loaded chunks whose hashes aren't listed in
`_buildManifest.js`, so I couldn't fetch them with curl) — this needs an actual
browser network trace to nail down, which no currently-available agent tool
can do.

Recommended fallback: **sequential ID sweep** against the item route in
§2, since invalid IDs 404 cleanly and cheaply. Observed real IDs (1366, 1375,
85838) span a wide, non-contiguous range — the "Object" module almost
certainly also covers the ~300,000-item Kupferstichkabinett prints/drawings
collection, not just the ~4,000 core paintings/sculptures, so don't assume a
small dense range. Treat this as best-effort discovery, not a real paginated
listing: track `processed_ids` as usual, checkpoint progress, and don't
attempt to infer a stopping point from a run of consecutive 404s (gaps are
expected) — cap the sweep at a fixed configurable upper bound instead and let
future runs raise it.

## 4. Artwork metadata fields (from the `item` JSON in §2)

- ID: `Id` (top-level, matches the URL's `{id}`)
- Title: `ObjDetailTitleTxt.LabelTxt_en` / `.LabelTxt` (German fallback when no
  English translation exists — several records only have `LabelTxt`)
- Artist: `ObjDetailArtistTxt.LabelTxt_en` (free text, embeds a
  `#RefArtist##{personId}##{name}#` token — the person ID is extractable if
  needed) — simpler: `ObjListArtistTxt.LabelTxt_en` is the clean display name
- Date: `ObjSortDateFromNum` (clean year int) or `ObjDetailDateTxt`
- Inventory number: `ObjDetailNumberTxt.LabelTxt_en`
- Physical dimensions (cm/in, not pixels): `ObjDetailDimensionTxt`
- Medium: `ObjDetailMaterialTechniqueTxt.LabelTxt_en`
- Description: `ObjDetailDescriptionTxt.LabelTxt_en`
- Image: `ObjDetailMultimediaRef.Items[0].Multimedia[0]` → `{thumb, full, mime}`
  (relative paths, see §6)

## 5. Rights filter — mixed collection, verifiable per-record field exists

Unlike LACMA, this is **not** a server-side-trust-only filter — there is a
real per-record field: `ObjDetailRightsTxt`.

Important finding: **this collection is not uniformly public domain.** All
three sampled objects (via the homepage's own "highlight" picks) are under
active copyright:

- `1366`: `"© 2026, ProLitteris, Zurich"`
- `1375`: `"© beim Künstler / the artist"`
- `85838`: `"© 2026 Helen Frankenthaler Foundation, Inc. / ProLitteris, Zurich"`

ProLitteris is the Swiss visual-arts copyright collecting society — its
presence means active rights management, not public domain. This makes sense:
Kunstmuseum Basel's collection is heavy on 20th-century/contemporary work,
and the museum's own marketing ("all images public domain") appears to
describe only part of the collection (older/pre-copyright works), not the
`Object` module as a whole.

**I did not find a confirmed public-domain example** — I only had the
homepage's curated modern-art highlights to sample from, and all three are
copyrighted. `ObjDetailRightsTxt` is clearly the correct field to gate on
(it's populated per-record with a real name/date when rights are held), but
BUILD/TRIAGE should confirm empirically what a genuine public-domain
record's `ObjDetailRightsTxt` looks like (absent field vs. an explicit
"gemeinfrei"/"public domain" string) before trusting the filter logic —
test against an old-master ID (e.g. search the Amerbach-Kabinett /
Holbein holdings) once real IDs for those are found via the §3 sweep.
**Do not treat "field is present" alone as the exclusion signal without
this check** — a truly-PD record might still carry a benign creditline in
an adjacent field.

## 6. Image URL format and renditions

`Multimedia` entries give two relative paths, resolved against the site's own
origin:
- `thumb`: `multimedia/{n}/multimedia-{id}.small.jpg`
- `full`: `multimedia/{n}/multimedia-{id}.large.jpg`

`large.jpg` for object 1366 was 232KB (`content-type: image/jpeg`) — a
reasonable size for on-screen 2D display use, not a thumbnail and not an
archival master. **Use `large` as `primary_image_url`.**

No `medium`/`xlarge`/`original` variants were found — only `small` and
`large` appear in the JSON. There's also a separate `ObjDetailDownloadImageLnk`
pointing to `https://download.kunstmuseumbasel.ch/#/ProcessRequest/{id}` — this
is a manual archival-image request portal (note the "ProcessRequest" naming),
not a direct-download URL. Do not use it; it's out of scope for an automated
downloader.

**Hotlink protection**: direct `curl` to a `multimedia/...jpg` URL with no
`Referer` returns `403`. Adding `Referer: https://sammlung.kunstmuseumbasel.ch/...`
(any page on the same origin) returns `200`. The downloader's HTTP client must
set this header on image requests.

## 7. Authentication requirements

None for the SSR item pages or images (beyond the Referer header in §6). No
API key needed — there is no public API to key against.

## 8. Approximate collection size

Public sources cite ~5,000 records / ~4,100 images with public-domain images
in "Collection Online" for the core paintings/sculptures/media-art holdings,
plus ~300,000 works on paper (drawings/prints, Kupferstichkabinett) being
progressively digitized — but given §5's finding that much of the sampled
content is actively copyrighted, the realistic yield of *actually
downloadable* (rights-clear) items is likely well under either headline
figure. No authoritative machine-readable total was found; BUILD/RUN should
treat whatever the §3 sweep discovers as the real number, not this estimate.
[Kunstmuseum Basel — Collection Online](https://kunstmuseumbasel.ch/en/collection/collectiononline)

## 9. Rate limiting / ToS

No published rate limits or terms of service for the `sammlung.` subdomain
were found (no `robots.txt`, no visible API docs to carry a rate-limit
clause). Given §3's sequential-ID-sweep approach will generate a large volume
of 404s, be conservative by default (similar to the `cma` museum's 80s/item
rate in `_MUSEUM_RATE_SECONDS`) rather than assuming AIC/Met-style headroom —
this is a smaller institution's site, not a museum with published API
capacity guarantees.

## 10. Pixel-dimension sourcing strategy

**(c) applies** — no width/height field exists anywhere in the item JSON
(only physical dimensions in cm/inches, `ObjDetailDimensionTxt`). Use
`fetch_remote_image_dimensions()` from `src/utils.py` against the `large`
rendition from §6 (with the required `Referer` header — the factory will need
to pass that through, same shape as `lacma.py`'s pattern) to get true pixel
dimensions via a partial/header read, without downloading the whole file.
