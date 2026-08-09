#!/usr/bin/env python3
"""
Independent museum verifier.

Builds the client exactly as main.py does, then takes a bounded live sample
(no writes to production data — see check E) and runs five checks:

  A  Non-empty          ≥10 metadata objects yielded within the time cap
  B  Licence filter     every sampled object is_public_domain == True
  C  URL well-formed    primary_image_url parses to http(s) with a real host
  D  Images are real    magic bytes + PIL decode + dims ≥ 200×200; no uniform placeholder
  E  DB writable        a sampled artwork round-trips through the real repository
                         write path against a throwaway temp SQLite file (never the
                         production DB). Catches schema/registration mismatches — e.g.
                         a museum code missing from the `museums` table — that a pure
                         API/image check can't see. This is exactly the class of bug
                         that let a full LACMA crawl complete with every one of 25,135
                         writes failing "Museum with code lacma not found".

Exit codes:
  0  PASS        all checks passed
  1  FAIL        at least one check failed (museum code is broken)
  2  INCONCLUSIVE  all image fetches hit network errors — retry later, don't triage

Emits a JSON result to stdout regardless of exit code.
"""

import json
import sys
import tempfile
import time
import urllib.request
import urllib.error
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

# Project root on path so we can import src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402  (comes from project deps)

from src.config import settings  # noqa: E402
from src.database.database import Database  # noqa: E402
from src.database.repository import ArtworkRepository  # noqa: E402
from main import get_museum_config  # noqa: E402

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------
METADATA_CAP = 30       # max metadata objects to collect
WALL_CLOCK_CAP = 300    # seconds — abort iteration after this
IMAGE_SAMPLE_MAX = 12   # how many URLs to actually fetch
MIN_METADATA = 10       # check A threshold
MIN_DIM = 200           # check D minimum image dimension (px)

# Magic-byte prefixes → format name
_MAGIC: List[tuple] = [
    (b"\xff\xd8", "JPEG"),
    (b"\x89PNG", "PNG"),
    (b"GIF8", "GIF"),
    (b"RIFF", "WEBP"),  # WEBP inside RIFF container
    (b"BM", "BMP"),
]

# Per-museum transport overrides: relax transport only, never checks A–D.
# Add a museum slug here when a CDN requires extra headers.
_TRANSPORT_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "rijks": {"extra_headers": {"Referer": "https://www.rijksmuseum.nl/"}},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_format(data: bytes) -> Optional[str]:
    for prefix, fmt in _MAGIC:
        if data[: len(prefix)] == prefix:
            return fmt
    return None


def _fetch_bytes(url: str, extra_headers: Dict[str, str]) -> Optional[bytes]:
    """Stream up to 512 KB from url. Returns None on any network failure."""
    headers = {"User-Agent": "ArtServe/1.0 Verifier", **extra_headers}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                return None
            return resp.read(512 * 1024)
    except Exception:
        return None


def _check_db_writable(slug: str, artwork: Any) -> Optional[str]:
    """Round-trip one sampled artwork through the real repository write path
    against a throwaway temp SQLite file. Returns None on success, or an error
    string on failure. Never touches the production database."""
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = Database(Path(tmp_dir) / "verify.sqlite")
            db.create_tables()
            with db.session_scope() as session:
                db.init_museums(session, museums=settings.museums)
                ArtworkRepository(session).create_or_update_artwork(
                    metadata=artwork, museum_code=slug
                )
        return None
    except Exception as exc:
        return f"DB write check failed: {exc}"


def _url_ok(url: Optional[str]) -> bool:
    if not url:
        return False
    if not url.startswith(("http://", "https://")):
        return False
    parts = url.split("/")
    host = parts[2] if len(parts) > 2 else ""
    if not host or "." not in host:
        return False
    if host in ("example.com", "localhost"):
        return False
    if url.startswith("data:"):
        return False
    return True


# ---------------------------------------------------------------------------
# Main verifier
# ---------------------------------------------------------------------------

def verify(slug: str) -> Dict[str, Any]:
    checks: Dict[str, Optional[bool]] = {
        "A_non_empty": None,
        "B_licence_filter": None,
        "C_url_well_formed": None,
        "D_images_real": None,
        "E_db_writable": None,
    }
    samples: List[Dict] = []
    image_results: List[Dict] = []
    errors: List[str] = []

    project_root = Path(__file__).resolve().parent.parent
    settings.initialize_paths(project_root)

    # --- Build the client (same path as main.py) ---------------------------
    try:
        museum_config = get_museum_config(slug)
    except Exception as exc:
        return _result("FAIL", f"get_museum_config failed: {exc}", checks, samples, image_results)

    client_params: Dict[str, Any] = {
        "museum_info": museum_config["museum_info"],
        "api_key": settings.museums[slug].api_key,
        "cache_file": None,  # no HTTP cache in verifier
        # no progress_tracker → avoids any progress-file writes
    }
    for key in ("format_filter",):
        if museum_config.get(key) is not None:
            client_params[key] = museum_config[key]

    try:
        client = museum_config["client_class"](**client_params)
    except Exception as exc:
        return _result("FAIL", f"Client construction failed: {exc}", checks, samples, image_results)

    # --- Phase 1: collect metadata sample ----------------------------------
    deadline = time.monotonic() + WALL_CLOCK_CAP
    bad_licence = False
    bad_url = False
    first_artwork = None

    try:
        for artwork in client.iter_collection(**museum_config["params"]):
            if time.monotonic() > deadline:
                errors.append("wall-clock cap hit")
                break
            if len(samples) >= METADATA_CAP:
                break

            if first_artwork is None:
                first_artwork = artwork

            if not artwork.is_public_domain:
                bad_licence = True

            url = artwork.primary_image_url or ""
            if not _url_ok(url):
                bad_url = True

            samples.append({"id": str(artwork.id), "title": artwork.title, "url": url})
    except Exception as exc:
        errors.append(f"iteration error: {exc}")

    checks["A_non_empty"] = len(samples) >= MIN_METADATA
    checks["B_licence_filter"] = not bad_licence
    checks["C_url_well_formed"] = not bad_url and bool(samples)

    if first_artwork is not None:
        db_error = _check_db_writable(slug, first_artwork)
        checks["E_db_writable"] = db_error is None
        if db_error:
            errors.append(db_error)

    if not checks["A_non_empty"]:
        reason = f"Only {len(samples)} metadata objects (need {MIN_METADATA})"
        if errors:
            reason += f"; {'; '.join(errors)}"
        # Zero samples with an iteration error = likely network down
        status = "INCONCLUSIVE" if (len(samples) == 0 and errors) else "FAIL"
        return _result(status, reason, checks, samples, image_results)

    # --- Phase 2: image verification ---------------------------------------
    extra_headers = _TRANSPORT_OVERRIDES.get(slug, {}).get("extra_headers", {})
    candidate_urls = [s["url"] for s in samples if _url_ok(s["url"])][:IMAGE_SAMPLE_MAX]

    pil_decoded = 0
    network_failures = 0
    sizes_seen: set = set()
    ok_count = 0

    for url in candidate_urls:
        data = _fetch_bytes(url, extra_headers)
        if data is None:
            network_failures += 1
            image_results.append({"url": url, "outcome": "network_error"})
            continue

        fmt = _detect_format(data)
        if not fmt:
            image_results.append({"url": url, "outcome": "bad_magic", "first": data[:8].hex()})
            continue

        try:
            img = Image.open(BytesIO(data))
            w, h = img.size
            pil_decoded += 1
            sizes_seen.add((len(data), w, h))

            if w < MIN_DIM or h < MIN_DIM:
                image_results.append({"url": url, "outcome": "too_small", "dims": f"{w}x{h}"})
            else:
                ok_count += 1
                image_results.append({"url": url, "outcome": "ok", "fmt": fmt, "dims": f"{w}x{h}"})
        except Exception as exc:
            image_results.append({"url": url, "outcome": "pil_failed", "error": str(exc)})

    if candidate_urls and network_failures == len(candidate_urls):
        checks["D_images_real"] = None  # cannot determine
        return _result(
            "INCONCLUSIVE",
            f"All {network_failures} image fetches failed with network errors",
            checks, samples, image_results,
        )

    # Uniformity check: all images identical tiny file → placeholder
    if len(sizes_seen) == 1 and len(candidate_urls) >= 5:
        (data_len, w, h), = sizes_seen
        if data_len < 50_000:
            checks["D_images_real"] = False
            return _result(
                "FAIL",
                f"Uniformity: all {len(candidate_urls)} images are the same tiny file ({data_len}B, {w}x{h})",
                checks, samples, image_results,
            )

    non_network = len(image_results) - network_failures
    too_small = sum(1 for r in image_results if r["outcome"] == "too_small")

    if pil_decoded < 2:
        checks["D_images_real"] = False
    elif non_network > 0 and too_small > non_network // 2:
        checks["D_images_real"] = False
    else:
        checks["D_images_real"] = True

    failed_checks = [k for k, v in checks.items() if v is False]
    status = "PASS" if not failed_checks else "FAIL"
    reason = None if status == "PASS" else f"Failed checks: {', '.join(failed_checks)}"
    return _result(status, reason, checks, samples[:10], image_results, pil_decoded=pil_decoded, errors=errors)


def _result(
    status: str,
    reason: Optional[str],
    checks: Dict,
    samples: List,
    image_results: List,
    pil_decoded: int = 0,
    errors: Optional[List[str]] = None,
) -> Dict:
    out: Dict[str, Any] = {"status": status, "checks": checks}
    if reason:
        out["reason"] = reason
    out["samples"] = samples
    out["image_results"] = image_results
    if pil_decoded:
        out["pil_decoded"] = pil_decoded
    if errors:
        out["errors"] = errors
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: verify_museum.py <slug>", file=sys.stderr)
        sys.exit(1)

    slug = sys.argv[1]
    result = verify(slug)
    print(json.dumps(result, indent=2))

    status = result.get("status")
    if status == "PASS":
        sys.exit(0)
    elif status == "INCONCLUSIVE":
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
