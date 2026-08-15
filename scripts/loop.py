#!/usr/bin/env python3
"""
ArtServe Museum Loop Driver — state machine for adding and running museum downloaders.

State machine (per museum):
  RESEARCH -> BUILD -> VALIDATE -> RUN -> DONE
                ^                    |
                +------ TRIAGE <-----+
                            |
                            v
                       NEEDS_HUMAN

Usage:
  python scripts/loop.py run              # start/resume the driver loop
  python scripts/loop.py status           # show queue state
  python scripts/loop.py add <slug>       # enqueue a new museum
  python scripts/loop.py resume <slug> [--from PHASE]
  python scripts/loop.py skip <slug>
  DATABASE_URL="postgresql://...@*.proxy.rlwy.net:PORT/railway" \
    python scripts/loop.py migrate <slug>   # manual metadata -> Postgres (not automatic; see cmd_migrate)

All persistent state lives in data/loop_state.json (atomic writes).
"""

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_FILE = PROJECT_ROOT / "data" / "loop_state.json"
NEEDS_HUMAN_FILE = PROJECT_ROOT / "NEEDS_HUMAN.md"
DOCS_DIR = PROJECT_ROOT / "docs"

POLL_BASE_SECONDS = 60        # baseline poll interval
MIN_STALL_SECONDS = 900       # 15 min — floor for stall threshold
STARTUP_GRACE_SECONDS = 3600  # 1 h — no stall checks during startup
DISK_MIN_GB = 5.0             # pause RUN below this free space

MAX_RESTART_ATTEMPTS = 2      # rung 1 before rung 2
MAX_BACKOFF_ATTEMPTS = 1      # rung 2 before rung 3
MAX_TRIAGE_ATTEMPTS = 3       # rung 3 before NEEDS_HUMAN

RATE_BACKOFF_MULTIPLIER = 2.0  # multiply rate_limit env var on rung 2

# files written by subprocesses (relative to museum data dir)
_RUN_EXIT_FILE = "run_exit.json"
_TRIAGE_VERDICT_FILE = "triage_verdict.json"
_PROGRESS_FILE = "cache/processed_ids.json"
_RUN_SUMMARY_FILE = "run_summary.json"

# Museum rate-limit defaults (seconds per item) — used to compute stall threshold
_MUSEUM_RATE_SECONDS: Dict[str, float] = {
    "aic": 1.0, "met": 2.0, "cma": 80.0, "mia": 5.0, "smk": 1.0,
    "nga": 1.0, "wellcome": 2.0, "loc": 1.0, "rijks": 1.0, "tepapa": 5.0,
}

# Files agents are allowed to touch (relative to project root); checked post-agent.
_BUILD_ALLOW = {"src/museums", "src/config.py", "src/museums/schemas.py", "main.py"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("loop")

# Shutdown flag set by SIGTERM
_shutdown = False


def _sigterm(sig, frame):  # noqa: ARG001
    global _shutdown
    log.info("SIGTERM received — finishing current step then exiting cleanly")
    _shutdown = True


signal.signal(signal.SIGTERM, _sigterm)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _default_museum_state(slug: str) -> Dict[str, Any]:
    return {
        "slug": slug,
        "phase": "RESEARCH",
        "attempts": {"research": 0, "build": 0, "validate": 0, "triage": 0, "restart": 0},
        "last_processed_count": 0,
        "last_progress_mtime": None,
        "run_pid": None,
        "agent_pid": None,
        "started_at": None,
        "rate_override": None,
        "baseline_total": None,
        "needs_human": False,
        "notes": [],
    }


def _load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"queue": [], "museums": {}}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


def _museum_data_dir(slug: str) -> Path:
    return PROJECT_ROOT / "data" / slug


def _progress_file(slug: str) -> Path:
    return _museum_data_dir(slug) / _PROGRESS_FILE


def _run_exit_file(slug: str) -> Path:
    return _museum_data_dir(slug) / _RUN_EXIT_FILE


def _triage_verdict_file(slug: str) -> Path:
    return _museum_data_dir(slug) / _TRIAGE_VERDICT_FILE


def _run_summary_file(slug: str) -> Path:
    return _museum_data_dir(slug) / _RUN_SUMMARY_FILE


def _note(ms: Dict, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ms["notes"].append(f"{ts} {msg}")


def _transition(ms: Dict, new_phase: str, reason: str = "") -> None:
    log.info("[%s] %s → %s  %s", ms["slug"], ms["phase"], new_phase, reason)
    ms["phase"] = new_phase
    _note(ms, f"→{new_phase}: {reason}")


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def notify(title: str, body: str) -> None:
    """Append to NEEDS_HUMAN.md and push via ntfy.sh (topic from env NTFY_TOPIC)."""
    ts = datetime.now(timezone.utc).isoformat()
    section = f"\n---\n## {ts}\n### {title}\n\n{body}\n"
    with NEEDS_HUMAN_FILE.open("a") as fh:
        fh.write(section)
    log.info("Wrote NEEDS_HUMAN.md: %s", title)

    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic:
        log.warning("NTFY_TOPIC not set — skipping push notification")
        return
    try:
        data = f"**{title}**\n\n{body[:2000]}".encode()
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=data,
            headers={"Content-Type": "text/plain; charset=utf-8", "Title": title[:250]},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("ntfy push sent (status %d)", resp.status)
    except Exception as exc:
        log.warning("ntfy push failed: %s", exc)


def escalate(ms: Dict, reason: str, extra: str = "") -> None:
    slug = ms["slug"]
    phase = ms["phase"]
    notes_tail = "\n".join(ms["notes"][-10:])
    body = (
        f"Museum: {slug}\nPhase: {phase}\nReason: {reason}\n\n"
        f"{extra}\n\nRecent notes:\n{notes_tail}\n\n"
        f"To resume: python scripts/loop.py resume {slug}\n"
        f"To skip:   python scripts/loop.py skip {slug}"
    )
    notify(f"ArtServe NEEDS_HUMAN — {slug}", body)
    ms["needs_human"] = True
    _transition(ms, "NEEDS_HUMAN", reason)


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _run_cmd(cmd: List[str], cwd: Path = PROJECT_ROOT, timeout: Optional[int] = None) -> int:
    """Run a command and return its exit code."""
    log.info("Running: %s", " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, cwd=cwd, timeout=timeout)
    return result.returncode


def _run_cmd_capture(cmd: List[str], cwd: Path = PROJECT_ROOT) -> tuple:
    """Run a command and return (exit_code, stdout, stderr)."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Agent worktree isolation
#
# RESEARCH/BUILD/TRIAGE spawn `claude -p` agents that edit files. The file-
# change audit (_allowed_files_changed) works by diffing against HEAD — but
# if it runs against PROJECT_ROOT directly, it sees *every* uncommitted change
# in the tree, including any made by a human working in the same checkout
# while the loop runs in the background (this happened for real: a harvard
# TRIAGE run was escalated to NEEDS_HUMAN for "touching" files that were
# actually mid-edit by an unrelated interactive session). Each agent instead
# gets a throwaway git worktree — a separate working directory + index
# sharing the same history — so its own diff-from-HEAD is provably isolated
# from anything happening elsewhere, no matter how concurrent.
# ---------------------------------------------------------------------------

WORKTREE_BASE = PROJECT_ROOT / ".loop-worktrees"


def _ensure_worktree_shared_dep_symlink() -> None:
    """pyproject.toml pins the editable `artserve-shared` dependency at the
    relative path `../ArtServe-Shared/python`, resolved from wherever
    pyproject.toml sits. Every agent worktree lives one level deeper than
    PROJECT_ROOT (PROJECT_ROOT/.loop-worktrees/<name>/), so that relative path
    resolves to .loop-worktrees/ArtServe-Shared/python instead of the real
    sibling next to PROJECT_ROOT — `uv run` (and anything that shells out to
    it, e.g. `uv run pytest`) fails outright inside any worktree without this.
    A one-time symlink at .loop-worktrees/ArtServe-Shared fixes the relative
    path for every worktree created under it, regardless of name/depth."""
    link = WORKTREE_BASE / "ArtServe-Shared"
    if link.exists() or link.is_symlink():
        return
    real = PROJECT_ROOT.parent / "ArtServe-Shared"
    if real.exists():
        link.symlink_to(real)


def _create_agent_worktree(slug: str, phase: str) -> Optional[Path]:
    """Create an isolated worktree at HEAD, sharing .venv/.env/data/ with the
    main tree (so the agent doesn't need to reinstall deps or lose access to
    runtime state like progress files and triage verdicts). Returns None on
    failure (caller should fall back to running against PROJECT_ROOT)."""
    WORKTREE_BASE.mkdir(exist_ok=True)
    _ensure_worktree_shared_dep_symlink()
    tag = uuid.uuid4().hex[:8]
    wt_path = WORKTREE_BASE / f"{slug}-{phase}-{tag}"
    branch = f"loop-work/{slug}-{phase}-{tag}"

    rc, _, err = _run_cmd_capture(
        ["git", "worktree", "add", "-b", branch, str(wt_path), "HEAD"]
    )
    if rc != 0:
        log.error("[%s] Failed to create isolated worktree: %s", slug, err.strip())
        return None

    try:
        venv_src = PROJECT_ROOT / ".venv"
        if venv_src.exists():
            (wt_path / ".venv").symlink_to(venv_src)

        env_src = PROJECT_ROOT / ".env"
        if env_src.exists():
            shutil.copy(env_src, wt_path / ".env")

        data_src = PROJECT_ROOT / "data"
        if data_src.exists():
            (wt_path / "data").symlink_to(data_src)
    except Exception as exc:
        log.error("[%s] Failed to prep worktree %s: %s", slug, wt_path, exc)
        _remove_agent_worktree(wt_path)
        return None

    return wt_path


def _remove_agent_worktree(wt_path: Path) -> None:
    """Remove the worktree and its throwaway branch (named `loop-work/<dir-name>`
    by _create_agent_worktree — derived rather than passed separately, since the
    two are always created together)."""
    rc, _, err = _run_cmd_capture(
        ["git", "worktree", "remove", "--force", str(wt_path)]
    )
    if rc != 0:
        log.warning("Failed to remove worktree %s: %s", wt_path, err.strip())

    branch = f"loop-work/{wt_path.name}"
    rc, _, err = _run_cmd_capture(["git", "branch", "-D", branch])
    if rc != 0:
        log.warning("Failed to delete worktree branch %s: %s", branch, err.strip())


# Symlinked into every agent worktree by _create_agent_worktree so agents share
# the main tree's venv/config/runtime-state — not agent-authored content, and
# excluded here because a symlinked directory doesn't match a trailing-slash
# .gitignore pattern (e.g. "data/"), so git would otherwise report it as an
# untracked file and _sync_worktree_changes would try to copy it as one.
_WORKTREE_INFRA_ENTRIES = {".venv", ".env", "data"}


def _changed_files_in(cwd: Path) -> List[str]:
    """Files changed (modified, added, or deleted) relative to HEAD, in `cwd`
    — both tracked modifications and new untracked files."""
    _, tracked_out, _ = _run_cmd_capture(["git", "diff", "--name-only", "HEAD"], cwd=cwd)
    _, untracked_out, _ = _run_cmd_capture(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=cwd
    )
    tracked = [f.strip() for f in tracked_out.splitlines() if f.strip()]
    untracked = [f.strip() for f in untracked_out.splitlines() if f.strip()]
    # dict.fromkeys dedupes while preserving order (a file could in principle
    # appear in both, though tracked/untracked are normally disjoint)
    all_changed = list(dict.fromkeys(tracked + untracked))
    return [f for f in all_changed if f not in _WORKTREE_INFRA_ENTRIES]


def _sync_worktree_changes(wt_path: Path, changed: List[str]) -> None:
    """Copy each changed/new file from the worktree back into PROJECT_ROOT
    (or remove it there if the agent deleted it in the worktree)."""
    for rel in changed:
        src = wt_path / rel
        dst = PROJECT_ROOT / rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif dst.exists():
            dst.unlink()


# ---------------------------------------------------------------------------
# Stall threshold
# ---------------------------------------------------------------------------

def _stall_threshold(slug: str, ms: Dict) -> int:
    """Compute adaptive stall threshold in seconds."""
    rate = ms.get("rate_override") or _MUSEUM_RATE_SECONDS.get(slug, 2.0)
    batch = 100  # save_batch_size from progress_tracker
    expected = rate * batch
    return max(MIN_STALL_SECONDS, int(4 * expected))


# ---------------------------------------------------------------------------
# Disk check
# ---------------------------------------------------------------------------

def _disk_ok() -> bool:
    usage = shutil.disk_usage(PROJECT_ROOT)
    free_gb = usage.free / (1024 ** 3)
    if free_gb < DISK_MIN_GB:
        log.warning("Disk space low: %.1fGB free (need %.1fGB)", free_gb, DISK_MIN_GB)
        return False
    return True


# ---------------------------------------------------------------------------
# Phase: RESEARCH
# ---------------------------------------------------------------------------

def _research(slug: str, ms: Dict, state: Dict) -> bool:
    """Invoke the RESEARCH agent, isolated in its own worktree. Returns True on success."""
    docs_dir = DOCS_DIR
    docs_dir.mkdir(parents=True, exist_ok=True)
    doc_file = docs_dir / f"{slug}.md"

    wt_path = _create_agent_worktree(slug, "research")
    run_cwd = wt_path or PROJECT_ROOT
    if wt_path is None:
        log.warning("[%s] Falling back to PROJECT_ROOT for RESEARCH (worktree creation failed)", slug)

    prompt = (
        f"You are researching the {slug} museum to build an ArtServe downloader.\n\n"
        f"Task: Investigate the museum's public API or data source for open-access artwork.\n"
        f"Output: Write a research document to docs/{slug}.md containing:\n"
        f"  1. Museum name and base URL\n"
        f"  2. API endpoint for listing public-domain artworks (exact URL + params)\n"
        f"  3. Pagination strategy (offset/cursor/page)\n"
        f"  4. Artwork metadata fields (id, title, artist, image URL)\n"
        f"  5. Rights filter — be rigorous, not just \"public domain\": identify the exact\n"
        f"     field/enum/value that signals the work is truly CC0 / unencumbered for reuse,\n"
        f"     not merely \"no known restrictions\" or a generic PD claim. State plainly\n"
        f"     whether this is a verifiable per-record field or only a server-side query\n"
        f"     filter that must be trusted without per-record confirmation (like LACMA's\n"
        f"     publicDomain param — see docs/lacma.md \xa75) — that distinction matters and\n"
        f"     must be called out explicitly, not glossed over.\n"
        f"  6. Image URL format and size/rendition options — enumerate every size the API\n"
        f"     offers (thumbnail/print/archival/etc. with approximate dimensions or file\n"
        f"     sizes if discoverable) and recommend which rendition is appropriate for\n"
        f"     on-screen 2D display use (a wallpaper app) — i.e. NOT a thumbnail (too small\n"
        f"     to look good full-screen) and NOT an archival multi-hundred-MB master (wastes\n"
        f"     bandwidth/storage for no display-quality benefit). Cite the actual size in\n"
        f"     pixels or bytes for whichever you recommend.\n"
        f"  7. Authentication requirements (if any)\n"
        f"  8. Approximate collection size (number of public-domain works)\n"
        f"  9. Any rate-limiting or terms of service constraints\n"
        f" 10. Pixel-dimension sourcing strategy — downloads are metadata-only by default\n"
        f"     (DOWNLOAD_IMAGES=false; no image bytes are saved to disk), but quality scoring\n"
        f"     needs image_pixel_width/image_pixel_height. Determine the CHEAPEST way to get\n"
        f"     real dimensions without downloading the full image:\n"
        f"       a) Does the listing/detail API response, or an IIIF manifest already being\n"
        f"          fetched for metadata, include width/height directly? (free — no extra\n"
        f"          request; this is how belvedere.py reads it off the IIIF canvas)\n"
        f"       b) If not, is there a lightweight sidecar endpoint (e.g. IIIF .../info.json)\n"
        f"          that returns dimensions in a tiny response?\n"
        f"       c) If neither exists, identify the smallest available image rendition and\n"
        f"          note that src/utils.py's fetch_remote_image_dimensions() can read just\n"
        f"          its header (a partial streamed read) to get true pixel dimensions without\n"
        f"          downloading the whole file — this is what lacma.py does against the\n"
        f"          ~900KB 'desktop' rendition, avoiding the 244MB archival TIFF.\n"
        f"     State which of (a)/(b)/(c) applies, with the exact field name or URL pattern.\n\n"
        f"Write only factual information you can verify from the API documentation or live requests.\n"
        f"Cite the exact endpoint URLs and field names you found.\n"
    )

    cmd = [
        "claude", "-p", prompt,
        "--allowedTools", "WebSearch,WebFetch,Read,Write",
        "--permission-mode", "bypassPermissions",
        "--max-turns", "35",
        "--output-format", "text",
    ]

    ms["attempts"]["research"] += 1
    log.info("[%s] Starting RESEARCH agent (attempt %d)", slug, ms["attempts"]["research"])

    try:
        proc = subprocess.Popen(cmd, cwd=run_cwd)
        ms["agent_pid"] = proc.pid
        _save_state(state)
        proc.wait()
        ms["agent_pid"] = None
        rc = proc.returncode
    except Exception as exc:
        log.error("[%s] RESEARCH agent failed to start: %s", slug, exc)
        ms["agent_pid"] = None
        if wt_path:
            _remove_agent_worktree(wt_path)
        return False

    changed = _changed_files_in(run_cwd) if wt_path else []
    if wt_path:
        _sync_worktree_changes(wt_path, changed)
        _remove_agent_worktree(wt_path)

    if rc != 0:
        log.error("[%s] RESEARCH agent exited with code %d", slug, rc)
        return False

    if not doc_file.exists():
        log.error("[%s] RESEARCH completed but docs/%s.md not written", slug, slug)
        return False

    log.info("[%s] RESEARCH done — docs/%s.md written", slug, slug)
    return True


# ---------------------------------------------------------------------------
# Phase: BUILD
# ---------------------------------------------------------------------------

def _log_forbidden_file_diagnostics(slug: str, cwd: Path, forbidden: List[str]) -> None:
    """Log what actually changed in each forbidden file, and whether that change
    exactly matches PROJECT_ROOT's own current uncommitted diff for the same path
    — the signature of the worktree having been branched from a stale/dirty HEAD
    rather than the agent genuinely editing out-of-scope files. Best-effort only;
    never let a diagnostic failure mask the real escalation."""
    for rel in forbidden:
        try:
            _, wt_diff, _ = _run_cmd_capture(["git", "diff", "HEAD", "--", rel], cwd=cwd)
            _, root_diff, _ = _run_cmd_capture(["git", "diff", "HEAD", "--", rel], cwd=PROJECT_ROOT)
            if wt_diff.strip() and wt_diff == root_diff:
                log.error(
                    "[%s]   %s: worktree diff is IDENTICAL to PROJECT_ROOT's own "
                    "uncommitted diff for this file — likely a stale/dirty HEAD, "
                    "not an agent edit. Commit or discard PROJECT_ROOT's pending "
                    "changes to this file, then retry.",
                    slug, rel,
                )
            else:
                snippet = "\n".join(wt_diff.splitlines()[:20])
                log.error("[%s]   %s: worktree-only change:\n%s", slug, rel, snippet)
        except Exception as exc:
            log.warning("[%s]   %s: diagnostic failed: %s", slug, rel, exc)


def _allowed_files_changed(slug: str, cwd: Path = PROJECT_ROOT) -> tuple:
    """Returns (all_allowed, changed_files, forbidden_files). all_allowed=True means
    no out-of-scope files. `cwd` should be the agent's own worktree — diffing
    PROJECT_ROOT directly would also pick up unrelated concurrent edits sitting
    in the main checkout (see the worktree-isolation note above)."""
    changed = _changed_files_in(cwd)
    allowed_prefixes = (
        f"src/museums/{slug}",
        "src/museums/schemas.py",
        "src/config.py",
        "main.py",
    )
    forbidden = [f for f in changed if not any(f.startswith(p) for p in allowed_prefixes)]
    return (len(forbidden) == 0, changed, forbidden)


def _build(slug: str, ms: Dict, state: Dict) -> bool:
    """Invoke the BUILD agent, isolated in its own worktree. Returns True on success."""
    doc_file = DOCS_DIR / f"{slug}.md"
    research_text = doc_file.read_text() if doc_file.exists() else "(no research doc found)"

    wt_path = _create_agent_worktree(slug, "build")
    run_cwd = wt_path or PROJECT_ROOT
    if wt_path is None:
        log.warning("[%s] Falling back to PROJECT_ROOT for BUILD (worktree creation failed)", slug)

    # Read existing museum file list for context
    ref_museums = ["aic", "met", "cma", "belvedere", "lacma"]
    existing = []
    for ref in ref_museums:
        ref_path = PROJECT_ROOT / "src" / "museums" / f"{ref}.py"
        if ref_path.exists():
            existing.append(f"src/museums/{ref}.py")

    prompt = (
        f"You are implementing an ArtServe museum downloader for {slug}.\n\n"
        f"ISOLATION: your current directory is a throwaway git worktree, not the main checkout —\n"
        f"it is a fully independent, complete copy of the repo at its own commit. Do everything\n"
        f"entirely within it. `uv run` (including `uv run pytest`) works normally here. Do NOT cd\n"
        f"to, read from, or copy/sync anything from another path (e.g. a path containing\n"
        f"'ArtAnalytics' that is not your own cwd, or anything reachable via the gitdir pointer in\n"
        f".git) — even if you notice it looks 'ahead' of what you see here, or that some test\n"
        f"failure looks unrelated to {slug}. This worktree is deliberately pinned to a fixed commit;\n"
        f"a mismatch with anything you happen to discover outside it is expected and NOT something\n"
        f"to reconcile. If a pre-existing test fails for reasons unrelated to {slug}, leave it and\n"
        f"note it in your summary — do not attempt to fix it.\n\n"
        f"Research document (docs/{slug}.md):\n{research_text}\n\n"
        f"Task: Create a fully working downloader in src/museums/{slug}.py following the patterns "
        f"in {', '.join(existing)}.\n\n"
        f"Required components:\n"
        f"  1. {slug.upper()}ArtworkFactory in src/museums/schemas.py — parses API JSON to ArtworkMetadata\n"
        f"  2. {slug.upper()}Client in src/museums/{slug}.py — implements _get_auth_header, "
        f"_iter_collection_impl, _get_artwork_details_impl\n"
        f"  3. {slug.upper()}ImageProcessor in src/museums/{slug}.py — override generate_filename "
        f"only if the prefix differs from '{slug.upper()}_'\n"
        f"  4. Register in src/config.py and main.py following the pattern for existing museums\n\n"
        f"Constraints:\n"
        f"  - Import from base classes in src/museums/base.py and src/download/progress_tracker.py\n"
        f"  - Do NOT duplicate process_image or generate_filename (they're in the base class)\n"
        f"  - Do NOT modify scripts/verify_museum.py, scripts/loop.py, or other museums' files\n"
        f"  - Do NOT write to data/ or any progress/state files\n"
        f"  - Ensure is_public_domain is set correctly per the research document's rights-filter\n"
        f"    finding (item 5) — if it's a server-side-trust-only filter (no per-record field to\n"
        f"    check), say so in a code comment rather than pretending it's independently verified\n"
        f"  - Use the size/rendition the research document recommended for 2D display (item 6) as\n"
        f"    primary_image_url — not the smallest thumbnail, not an archival master\n"
        f"  - Populate ArtworkMetadata.image_pixel_width/image_pixel_height using the strategy the\n"
        f"    research document identified (item 10): pull width/height straight off the API/manifest\n"
        f"    response if it's already there (see belvedere.py's IIIF canvas extraction — free, no\n"
        f"    extra request), otherwise call fetch_remote_image_dimensions() from src/utils.py against\n"
        f"    the display-appropriate rendition (see lacma.py's factory for the pattern). Do NOT rely on\n"
        f"    downloading the full image to measure it — DOWNLOAD_IMAGES defaults to false, so that path\n"
        f"    won't run in production and dimensions would silently end up NULL\n"
        f"  - If a dimension fetch is added to a factory, mock it (patch the imported name in\n"
        f"    src.museums.schemas) in that museum's tests — do not let tests hit a live network host\n"
        f"  - Run uv run pytest tests/ -x -q to verify nothing is broken\n"
    )

    cmd = [
        "claude", "-p", prompt,
        "--allowedTools", f"Read,Write,Edit,Bash",
        "--disallowedTools", "WebSearch,WebFetch",
        "--permission-mode", "bypassPermissions",
        "--max-turns", "50",
        "--output-format", "text",
    ]

    ms["attempts"]["build"] += 1
    log.info("[%s] Starting BUILD agent (attempt %d)", slug, ms["attempts"]["build"])

    try:
        proc = subprocess.Popen(cmd, cwd=run_cwd)
        ms["agent_pid"] = proc.pid
        _save_state(state)
        proc.wait()
        ms["agent_pid"] = None
        rc = proc.returncode
    except Exception as exc:
        log.error("[%s] BUILD agent failed to start: %s", slug, exc)
        ms["agent_pid"] = None
        if wt_path:
            _remove_agent_worktree(wt_path)
        return False

    # Audit changed files against the isolated worktree — never PROJECT_ROOT
    # directly, which could be dirty from unrelated concurrent work
    all_ok, changed, forbidden = _allowed_files_changed(slug, cwd=run_cwd)
    if not all_ok:
        log.error("[%s] BUILD touched forbidden files: %s", slug, forbidden)
        _log_forbidden_file_diagnostics(slug, run_cwd, forbidden)
        if wt_path:
            log.error("[%s] Worktree left in place for inspection: %s", slug, wt_path)
        escalate(ms, f"BUILD agent touched forbidden files: {forbidden}")
        return False

    if wt_path:
        _sync_worktree_changes(wt_path, changed)
        _remove_agent_worktree(wt_path)

    museum_file = PROJECT_ROOT / "src" / "museums" / f"{slug}.py"
    if not museum_file.exists():
        log.error("[%s] BUILD did not create src/museums/%s.py", slug, slug)
        return False

    if rc != 0:
        log.error("[%s] BUILD agent exited with code %d", slug, rc)
        return False

    log.info("[%s] BUILD done — changed: %s", slug, changed)
    return True


# ---------------------------------------------------------------------------
# Phase: VALIDATE
# ---------------------------------------------------------------------------

def _validate(slug: str, ms: Dict) -> str:
    """Run the verifier. Returns 'PASS', 'FAIL', or 'INCONCLUSIVE'."""
    # Always run the committed verifier (agent cannot have modified it if git checkout works)
    _run_cmd(["git", "checkout", "scripts/verify_museum.py"], cwd=PROJECT_ROOT)

    rc, stdout, stderr = _run_cmd_capture(
        ["uv", "run", "python", "scripts/verify_museum.py", slug],
        cwd=PROJECT_ROOT,
    )

    log.info("[%s] Verifier exit code %d", slug, rc)
    if stdout:
        log.info("[%s] Verifier output: %s", slug, stdout[:500])

    try:
        result = json.loads(stdout)
        status = result.get("status", "FAIL")
    except Exception:
        status = "FAIL"
        log.error("[%s] Could not parse verifier JSON: %s", slug, stdout[:200])

    if rc == 0 and status == "PASS":
        # Record baseline_total from verifier samples count as proxy
        if ms.get("baseline_total") is None:
            # Try to get from collection_info
            try:
                from src.config import settings
                settings.initialize_paths(PROJECT_ROOT)
                from main import get_museum_config
                cfg = get_museum_config(slug)
                client = cfg["client_class"](
                    museum_info=cfg["museum_info"],
                    api_key=settings.museums[slug].api_key,
                    cache_file=None,
                )
                info = client.get_collection_info()
                ms["baseline_total"] = info.get("total_objects")
                log.info("[%s] baseline_total = %s", slug, ms["baseline_total"])
            except Exception as exc:
                log.warning("[%s] Could not fetch baseline_total: %s", slug, exc)
        return "PASS"
    elif rc == 2:
        return "INCONCLUSIVE"
    return "FAIL"


# ---------------------------------------------------------------------------
# Phase: RUN (poll loop)
# ---------------------------------------------------------------------------

def _read_processed_count(slug: str) -> int:
    pf = _progress_file(slug)
    if not pf.exists():
        return 0
    try:
        data = json.loads(pf.read_text())
        return len(data.get("processed_ids", []))
    except Exception:
        return 0


def _read_run_summary(slug: str) -> Optional[Dict]:
    sf = _run_summary_file(slug)
    if not sf.exists():
        return None
    try:
        return json.loads(sf.read_text())
    except Exception:
        return None


def _write_run_exit(slug: str, exit_code: int) -> None:
    ef = _run_exit_file(slug)
    ef.parent.mkdir(parents=True, exist_ok=True)
    ef.write_text(json.dumps({"exit_code": exit_code, "ts": datetime.now(timezone.utc).isoformat()}))


def _read_run_exit(slug: str) -> Optional[int]:
    ef = _run_exit_file(slug)
    if not ef.exists():
        return None
    try:
        return json.loads(ef.read_text()).get("exit_code")
    except Exception:
        return None


def _build_run_cmd(slug: str, ms: Dict) -> List[str]:
    cmd = [sys.executable, "main.py", "-m", slug]
    return cmd


def _run_download(slug: str, ms: Dict, state: Dict) -> str:
    """
    Start and poll a download subprocess.

    Returns: 'DONE' | 'CRASH' | 'STALL' | 'DISK_FULL'
    """
    if not _disk_ok():
        return "DISK_FULL"

    env = os.environ.copy()
    if ms.get("rate_override"):
        env["RATE_LIMIT_DELAY"] = str(ms["rate_override"])

    cmd = _build_run_cmd(slug, ms)
    # Remove old run exit file so we don't read a stale one
    _run_exit_file(slug).unlink(missing_ok=True)

    log.info("[%s] Starting download: %s", slug, " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=env)
    ms["run_pid"] = proc.pid
    ms["started_at"] = datetime.now(timezone.utc).isoformat()
    ms["last_processed_count"] = _read_processed_count(slug)
    ms["last_progress_mtime"] = time.time()
    ms["attempts"]["restart"] += 1
    _save_state(state)

    stall_threshold = _stall_threshold(slug, ms)
    poll_interval = min(stall_threshold // 4, POLL_BASE_SECONDS * 5)
    startup_deadline = time.monotonic() + STARTUP_GRACE_SECONDS

    log.info(
        "[%s] Poll interval=%ds, stall threshold=%ds, startup grace=%ds",
        slug, poll_interval, stall_threshold, STARTUP_GRACE_SECONDS,
    )

    while not _shutdown:
        time.sleep(poll_interval)

        # Check if process is still alive
        rc = proc.poll()
        if rc is not None:
            _write_run_exit(slug, rc)
            ms["run_pid"] = None
            _save_state(state)

            if rc == 0:
                summary = _read_run_summary(slug)
                if summary and summary.get("reached_end_of_collection") and summary.get("total_processed", 0) > 0:
                    return "DONE"
                log.warning("[%s] Exit 0 but run_summary missing end-of-collection flag", slug)
                return "CRASH"
            log.error("[%s] Download exited with code %d", slug, rc)
            return "CRASH"

        # Disk check
        if not _disk_ok():
            proc.terminate()
            proc.wait()
            ms["run_pid"] = None
            _save_state(state)
            return "DISK_FULL"

        # Stall detection (skip during startup grace)
        if time.monotonic() > startup_deadline:
            count_now = _read_processed_count(slug)
            mtime_now = _progress_file(slug).stat().st_mtime if _progress_file(slug).exists() else ms["last_progress_mtime"]

            progress_advanced = (count_now > ms["last_processed_count"])
            time_since_progress = time.time() - (mtime_now or time.time())

            if count_now > ms["last_processed_count"]:
                ms["last_processed_count"] = count_now
                ms["last_progress_mtime"] = mtime_now
                _save_state(state)

            if not progress_advanced and time_since_progress > stall_threshold:
                log.warning(
                    "[%s] STALL detected: %d items, %.1fh since last progress (threshold %.1fh)",
                    slug, count_now, time_since_progress / 3600, stall_threshold / 3600,
                )
                proc.terminate()
                proc.wait()
                ms["run_pid"] = None
                _save_state(state)
                return "STALL"

    # Shutdown signal: leave child running; it will be reattached on restart
    log.info("[%s] Shutdown — leaving child pid=%d running", slug, proc.pid)
    return "SHUTDOWN"


def _handle_run_outcome(slug: str, ms: Dict, state: Dict, outcome: str) -> None:
    """Apply restart ladder based on run outcome."""
    if outcome == "DONE":
        _transition(ms, "DONE", "clean exit + end-of-collection confirmed")
        return

    if outcome == "SHUTDOWN":
        return  # don't change phase; resume on next driver start

    if outcome == "DISK_FULL":
        escalate(ms, "Disk space below minimum threshold — free space and resume", extra="")
        return

    # CRASH or STALL — apply restart ladder
    restart_count = ms["attempts"]["restart"]
    triage_count = ms["attempts"]["triage"]

    rung_1_exhausted = restart_count > MAX_RESTART_ATTEMPTS
    rung_2_exhausted = restart_count > MAX_RESTART_ATTEMPTS + MAX_BACKOFF_ATTEMPTS

    if not rung_1_exhausted:
        log.info("[%s] %s — rung 1 restart (attempt %d/%d)", slug, outcome, restart_count, MAX_RESTART_ATTEMPTS)
        _note(ms, f"Rung 1 restart after {outcome}")
        # Stay in RUN phase; next loop iteration will restart
    elif not rung_2_exhausted:
        # Rung 2: increase rate delay
        current = ms.get("rate_override") or _MUSEUM_RATE_SECONDS.get(slug, 2.0)
        ms["rate_override"] = round(current * RATE_BACKOFF_MULTIPLIER, 2)
        log.info("[%s] %s — rung 2: rate backoff to %.1fs/item", slug, outcome, ms["rate_override"])
        _note(ms, f"Rung 2 rate backoff to {ms['rate_override']}s after {outcome}")
    elif triage_count < MAX_TRIAGE_ATTEMPTS:
        _transition(ms, "TRIAGE", f"after {outcome} ({restart_count} restarts)")
    else:
        escalate(ms, f"Exhausted {MAX_TRIAGE_ATTEMPTS} triage attempts after {outcome}")


# ---------------------------------------------------------------------------
# Phase: TRIAGE
# ---------------------------------------------------------------------------

def _triage(slug: str, ms: Dict, state: Dict) -> bool:
    """Invoke the TRIAGE agent, isolated in its own worktree.
    Returns True if agent says 'fixed' AND verifier passes."""
    # Read context
    doc_file = DOCS_DIR / f"{slug}.md"
    research_text = doc_file.read_text() if doc_file.exists() else "(no research doc)"

    wt_path = _create_agent_worktree(slug, "triage")
    run_cwd = wt_path or PROJECT_ROOT
    if wt_path is None:
        log.warning("[%s] Falling back to PROJECT_ROOT for TRIAGE (worktree creation failed)", slug)

    pf = _progress_file(slug)
    count = ms.get("last_processed_count", 0)
    elapsed_h = 0
    if ms.get("started_at"):
        try:
            start = datetime.fromisoformat(ms["started_at"].replace("Z", "+00:00"))
            elapsed_h = (datetime.now(timezone.utc) - start).total_seconds() / 3600
        except Exception:
            pass

    prior_notes = "\n".join(ms["notes"][-15:])
    run_exit = _read_run_exit(slug)

    prompt = (
        f"TRIAGE task for ArtServe museum: {slug}\n\n"
        f"ISOLATION: your current directory is a throwaway git worktree, not the main checkout —\n"
        f"it is a fully independent, complete copy of the repo at its own commit. Do everything\n"
        f"entirely within it. `uv run` (including `uv run pytest`) works normally here. Do NOT cd\n"
        f"to, read from, or copy/sync anything from another path (e.g. a path containing\n"
        f"'ArtAnalytics' that is not your own cwd, or anything reachable via the gitdir pointer in\n"
        f".git) — even if you notice it looks 'ahead' of what you see here. This worktree is\n"
        f"deliberately pinned to a fixed commit; a mismatch with anything outside it is expected\n"
        f"and NOT something to reconcile. If a pre-existing test fails for reasons unrelated to\n"
        f"{slug}, leave it — do not attempt to fix it.\n\n"
        f"Failure signal:\n"
        f"  - Exit code: {run_exit}\n"
        f"  - Processed count: {count}\n"
        f"  - Elapsed: {elapsed_h:.1f}h\n\n"
        f"Prior attempts:\n{prior_notes}\n\n"
        f"Research document:\n{research_text[:3000]}\n\n"
        f"Allowed files to modify:\n"
        f"  - src/museums/{slug}.py\n"
        f"  - src/museums/schemas.py (only the {slug} factory)\n"
        f"  - src/config.py (only the {slug} entry)\n\n"
        f"Forbidden: scripts/verify_museum.py, scripts/loop.py, base.py, "
        f"progress_tracker.py, any other museum's files, data/ files\n\n"
        f"After your fix, run: uv run pytest tests/ -x -q\n\n"
        f"Write your verdict to data/{slug}/triage_verdict.json:\n"
        f'  {{"verdict": "fixed"|"cannot_fix", "summary": "...", "hypothesis": "..."}}\n'
    )

    cmd = [
        "claude", "-p", prompt,
        "--allowedTools", "Read,Write,Edit,Bash",
        "--disallowedTools", "WebSearch,WebFetch",
        "--permission-mode", "bypassPermissions",
        "--max-turns", "30",
        "--output-format", "text",
    ]

    ms["attempts"]["triage"] += 1
    log.info("[%s] Starting TRIAGE agent (attempt %d)", slug, ms["attempts"]["triage"])

    try:
        proc = subprocess.Popen(cmd, cwd=run_cwd)
        ms["agent_pid"] = proc.pid
        _save_state(state)
        proc.wait()
        ms["agent_pid"] = None
        rc = proc.returncode
    except Exception as exc:
        log.error("[%s] TRIAGE agent failed to start: %s", slug, exc)
        ms["agent_pid"] = None
        if wt_path:
            _remove_agent_worktree(wt_path)
        return False

    # Audit changed files against the isolated worktree — never PROJECT_ROOT
    # directly, which could be dirty from unrelated concurrent work
    all_ok, changed, forbidden = _allowed_files_changed(slug, cwd=run_cwd)
    if not all_ok:
        log.error("[%s] TRIAGE touched forbidden files: %s", slug, forbidden)
        _log_forbidden_file_diagnostics(slug, run_cwd, forbidden)
        if wt_path:
            log.error("[%s] Worktree left in place for inspection: %s", slug, wt_path)
        escalate(ms, f"TRIAGE agent touched forbidden files: {forbidden}")
        return False

    if wt_path:
        _sync_worktree_changes(wt_path, changed)
        _remove_agent_worktree(wt_path)

    # Read verdict
    verdict_file = _triage_verdict_file(slug)
    if not verdict_file.exists():
        log.error("[%s] TRIAGE did not write triage_verdict.json", slug)
        return False

    try:
        verdict = json.loads(verdict_file.read_text())
    except Exception as exc:
        log.error("[%s] Could not parse triage_verdict.json: %s", slug, exc)
        return False

    _note(ms, f"TRIAGE verdict={verdict.get('verdict')} hypothesis={verdict.get('hypothesis','')[:100]}")

    if verdict.get("verdict") == "cannot_fix":
        escalate(ms, f"Triage cannot_fix: {verdict.get('hypothesis','')}", extra=str(verdict))
        return False

    # Agent claims fixed — verify independently
    verifier_status = _validate(slug, ms)
    if verifier_status != "PASS":
        log.error("[%s] Triage claimed fixed but verifier says %s", slug, verifier_status)
        return False

    log.info("[%s] TRIAGE fix confirmed by verifier", slug)
    return True


# ---------------------------------------------------------------------------
# Main driver loop
# ---------------------------------------------------------------------------

def _pick_active(state: Dict) -> Optional[str]:
    """Return slug of the first museum that still needs work."""
    for slug in state.get("queue", []):
        ms = state["museums"].get(slug, {})
        if ms.get("phase") not in ("DONE", "NEEDS_HUMAN") and not ms.get("needs_human"):
            return slug
    return None


def _check_prior_run(slug: str, ms: Dict) -> Optional[str]:
    """
    Called at the start of a RUN step to handle driver-restart scenarios.

    Returns:
      None        — no prior run in progress; start fresh
      "DONE"      — prior run finished cleanly; go to DONE
      "CRASH"     — prior run crashed; apply restart ladder
      "reattach"  — prior child is still alive; caller should call _poll_existing_pid
    """
    pid = ms.get("run_pid")
    if not pid:
        return None

    if _pid_alive(pid):
        log.info("[%s] Driver restarted — existing child pid=%d still alive; reattaching", slug, pid)
        return "reattach"

    # Child is gone
    exit_code = _read_run_exit(slug)
    if exit_code is None:
        log.warning("[%s] Child pid=%d gone with no exit marker — treating as CRASH", slug, pid)
        ms["run_pid"] = None
        return "CRASH"

    ms["run_pid"] = None
    if exit_code == 0:
        summary = _read_run_summary(slug)
        if summary and summary.get("reached_end_of_collection") and summary.get("total_processed", 0) > 0:
            return "DONE"
        log.warning("[%s] Exit 0 but run_summary missing end-of-collection flag", slug)
        return "CRASH"

    log.error("[%s] Child exited with code %d", slug, exit_code)
    return "CRASH"


def _poll_existing_pid(slug: str, pid: int, ms: Dict, state: Dict) -> str:
    """
    Poll a download subprocess that was started by a previous driver incarnation.
    Uses _pid_alive() + run_exit.json instead of a subprocess.Popen object.

    Returns same outcome strings as _run_download.
    """
    stall_threshold = _stall_threshold(slug, ms)
    poll_interval = min(stall_threshold // 4, POLL_BASE_SECONDS * 5)
    startup_deadline = (
        time.monotonic() + STARTUP_GRACE_SECONDS
        if ms.get("last_processed_count", 0) == 0
        else time.monotonic()
    )

    log.info("[%s] Polling existing pid=%d (stall threshold=%.1fh)", slug, pid, stall_threshold / 3600)

    while not _shutdown:
        time.sleep(poll_interval)

        if not _pid_alive(pid):
            exit_code = _read_run_exit(slug)
            ms["run_pid"] = None
            _save_state(state)
            if exit_code is None or exit_code != 0:
                return "CRASH"
            summary = _read_run_summary(slug)
            if summary and summary.get("reached_end_of_collection") and summary.get("total_processed", 0) > 0:
                return "DONE"
            return "CRASH"

        if not _disk_ok():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            ms["run_pid"] = None
            _save_state(state)
            return "DISK_FULL"

        if time.monotonic() > startup_deadline:
            count_now = _read_processed_count(slug)
            mtime_now = (
                _progress_file(slug).stat().st_mtime
                if _progress_file(slug).exists()
                else ms.get("last_progress_mtime", time.time())
            )
            time_since = time.time() - (mtime_now or time.time())

            if count_now > ms.get("last_processed_count", 0):
                ms["last_processed_count"] = count_now
                ms["last_progress_mtime"] = mtime_now
                _save_state(state)

            if time_since > stall_threshold:
                log.warning(
                    "[%s] STALL: %d items, %.1fh since last write (threshold %.1fh)",
                    slug, count_now, time_since / 3600, stall_threshold / 3600,
                )
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                ms["run_pid"] = None
                _save_state(state)
                return "STALL"

    log.info("[%s] Shutdown — leaving child pid=%d running", slug, pid)
    return "SHUTDOWN"


def step(slug: str, state: Dict) -> None:
    """Execute one state-machine step for slug."""
    ms = state["museums"][slug]
    phase = ms["phase"]

    if phase == "RESEARCH":
        ok = _research(slug, ms, state)
        if ok:
            _transition(ms, "BUILD", "research doc written")
        else:
            if ms["attempts"]["research"] >= 2:
                escalate(ms, "RESEARCH agent failed twice")
            else:
                _note(ms, "RESEARCH attempt failed; will retry")

    elif phase == "BUILD":
        ok = _build(slug, ms, state)
        if ok:
            _transition(ms, "VALIDATE", "museum file created")
        else:
            if ms["attempts"]["build"] >= 2:
                escalate(ms, "BUILD agent failed twice")
            else:
                _note(ms, "BUILD attempt failed; will retry")

    elif phase == "VALIDATE":
        ms["attempts"]["validate"] += 1
        result = _validate(slug, ms)
        if result == "PASS":
            _transition(ms, "RUN", "verifier PASS")
        elif result == "INCONCLUSIVE":
            log.warning("[%s] Verifier INCONCLUSIVE — will retry", slug)
            _note(ms, "Verifier INCONCLUSIVE (network?)")
            time.sleep(300)  # wait 5 min before retrying
        else:
            log.error("[%s] Verifier FAIL — going to TRIAGE", slug)
            _transition(ms, "TRIAGE", "verifier FAIL after BUILD")

    elif phase == "RUN":
        prior = _check_prior_run(slug, ms)
        if prior == "reattach":
            outcome = _poll_existing_pid(slug, ms["run_pid"], ms, state)
        elif prior in ("DONE", "CRASH"):
            outcome = prior
        else:
            outcome = _run_download(slug, ms, state)

        if outcome != "SHUTDOWN":
            _handle_run_outcome(slug, ms, state, outcome)

    elif phase == "TRIAGE":
        fixed = _triage(slug, ms, state)
        if fixed:
            # Reset restart counter so it gets fresh rung-1 attempts
            ms["attempts"]["restart"] = 0
            _transition(ms, "RUN", "triage fix verified")
        else:
            if ms["attempts"]["triage"] >= MAX_TRIAGE_ATTEMPTS:
                escalate(ms, f"Exhausted {MAX_TRIAGE_ATTEMPTS} triage attempts")

    elif phase in ("DONE", "NEEDS_HUMAN"):
        pass  # nothing to do

    _save_state(state)


def driver_loop() -> None:
    """Main blocking loop — runs until all museums are DONE/NEEDS_HUMAN or SIGTERM."""
    log.info("ArtServe museum loop driver starting")
    while not _shutdown:
        state = _load_state()
        slug = _pick_active(state)

        if slug is None:
            total = len(state.get("queue", []))
            done = sum(1 for s in state.get("queue", [])
                       if state["museums"].get(s, {}).get("phase") in ("DONE", "NEEDS_HUMAN"))
            if total == 0:
                log.info("Queue is empty — nothing to do. Add museums with: loop.py add <slug>")
            elif done == total:
                log.info("All %d museums are DONE or NEEDS_HUMAN — loop complete", total)
            else:
                log.info("No active museums — waiting 60s")
            if _shutdown:
                break
            time.sleep(60)
            continue

        log.info("[%s] Active — phase=%s", slug, state["museums"][slug]["phase"])
        step(slug, state)

        if _shutdown:
            break

    log.info("Driver loop exiting")


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_run(args) -> None:
    driver_loop()


def cmd_add(args) -> None:
    state = _load_state()
    slug = args.slug
    if slug in state.get("queue", []):
        print(f"{slug} already in queue (phase={state['museums'][slug]['phase']})")
        return
    state.setdefault("queue", []).append(slug)
    state.setdefault("museums", {})[slug] = _default_museum_state(slug)
    _save_state(state)
    print(f"Added {slug} to queue")


def cmd_status(args) -> None:
    state = _load_state()
    queue = state.get("queue", [])
    if not queue:
        print("Queue is empty")
        return
    for slug in queue:
        ms = state["museums"].get(slug, {})
        phase = ms.get("phase", "?")
        attempts = ms.get("attempts", {})
        notes_last = (ms.get("notes") or ["(none)"])[-1]
        print(f"  {slug:<12} {phase:<14} restarts={attempts.get('restart',0)} "
              f"triage={attempts.get('triage',0)}  last={notes_last[-60:]}")


def cmd_resume(args) -> None:
    state = _load_state()
    slug = args.slug
    if slug not in state.get("museums", {}):
        print(f"{slug} not in state — use 'add' first")
        return
    ms = state["museums"][slug]
    target_phase = args.from_phase or ms["phase"]
    ms["needs_human"] = False
    ms["phase"] = target_phase.upper()
    _note(ms, f"Manual resume to {target_phase}")
    _save_state(state)
    print(f"Resumed {slug} at phase {target_phase}")


def cmd_skip(args) -> None:
    state = _load_state()
    slug = args.slug
    if slug not in state.get("museums", {}):
        print(f"{slug} not in state")
        return
    ms = state["museums"][slug]
    _note(ms, "Manually skipped")
    ms["phase"] = "DONE"
    ms["needs_human"] = False
    _save_state(state)
    print(f"Marked {slug} as DONE (skipped)")


def cmd_migrate(args) -> None:
    """Manually migrate one museum's local SQLite metadata to Railway Postgres.

    Deliberately NOT wired into the RUN->DONE autonomous transition — the
    schema between ArtAnalytics's local model and the live Postgres table has
    drifted before (silently, until an actual insert hit it) and this writes
    to a production DB backing a paid app. Run by hand after a museum
    completes, and only promote to a real loop phase once that's boring.
    Requires DATABASE_URL (the Railway *public* proxy URL — this runs from
    outside Railway's network, so the private railway.internal host is
    unreachable) set in the environment.
    """
    slug = args.slug
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL is not set. Export the Railway public proxy URL first, e.g.:")
        print('  DATABASE_URL="postgresql://...@*.proxy.rlwy.net:PORT/railway" python scripts/loop.py migrate ' + slug)
        sys.exit(1)

    cmd = [
        "uv", "run", "python", "-m", "src.database.migrate_to_postgres",
        "--museum", slug,
    ]
    print(f"Migrating '{slug}' metadata to Postgres...")
    rc = subprocess.run(cmd, cwd=PROJECT_ROOT).returncode

    state = _load_state()
    ms = state.get("museums", {}).get(slug)
    if ms is not None:
        _note(ms, f"Manual migrate to Postgres: {'success' if rc == 0 else f'FAILED (exit {rc})'}")
        _save_state(state)

    if rc != 0:
        print(f"Migration failed (exit {rc}).")
        sys.exit(rc)
    print("Migration complete.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="ArtServe museum loop driver")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="Start/resume the driver loop")

    p_add = sub.add_parser("add", help="Enqueue a new museum")
    p_add.add_argument("slug")

    sub.add_parser("status", help="Show queue state")

    p_resume = sub.add_parser("resume", help="Resume a museum (clears NEEDS_HUMAN)")
    p_resume.add_argument("slug")
    p_resume.add_argument("--from", dest="from_phase", metavar="PHASE",
                          choices=["RESEARCH", "BUILD", "VALIDATE", "RUN", "TRIAGE"])

    p_skip = sub.add_parser("skip", help="Mark a museum as done (skip it)")
    p_skip.add_argument("slug")

    p_migrate = sub.add_parser(
        "migrate",
        help="Manually migrate one museum's metadata to Railway Postgres (requires DATABASE_URL)",
    )
    p_migrate.add_argument("slug")

    args = parser.parse_args()

    dispatch = {
        "run": cmd_run,
        "add": cmd_add,
        "status": cmd_status,
        "resume": cmd_resume,
        "skip": cmd_skip,
        "migrate": cmd_migrate,
    }

    handler = dispatch.get(args.command)
    if not handler:
        parser.print_help()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()
