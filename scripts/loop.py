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

RUN is non-blocking: the driver health-checks (or starts, up to
MAX_CONCURRENT_RUNS) every museum in RUN each tick, then advances exactly one
other museum through its next RESEARCH/BUILD/VALIDATE/TRIAGE step — those
stay serial since they invoke agents and share the repo/worktree
infrastructure, but a multi-day download no longer blocks the rest of the
queue. See _tick_run / driver_loop.

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
MAX_VALIDATE_INCONCLUSIVE = 3 # consecutive INCONCLUSIVE verifier runs before NEEDS_HUMAN

RATE_BACKOFF_MULTIPLIER = 2.0  # multiply rate_limit env var on rung 2

# RUN downloads happen in the background (see _tick_run) so a multi-day crawl
# doesn't block RESEARCH/BUILD/VALIDATE/TRIAGE for the rest of the queue. Cap
# concurrency to bound this Pi's bandwidth/disk contention.
MAX_CONCURRENT_RUNS = 2

# files written by subprocesses (relative to museum data dir)
_RUN_EXIT_FILE = "run_exit.json"
_TRIAGE_VERDICT_FILE = "triage_verdict.json"
_PROGRESS_FILE = "cache/processed_ids.json"
_RUN_SUMMARY_FILE = "run_summary.json"
_RESEARCH_SUMMARY_FILE = "research_summary.json"

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
        "attempts": {
            "research": 0, "build": 0, "validate": 0, "validate_inconclusive": 0,
            "triage": 0, "restart": 0,
        },
        "last_processed_count": 0,
        "last_progress_mtime": None,
        "run_pid": None,
        "agent_pid": None,
        "started_at": None,
        "rate_override": None,
        "baseline_total": None,
        "needs_human": False,
        "notes": [],
        # Non-blocking backoff: when set (epoch seconds), _pick_active skips
        # this museum until then instead of the driver sleeping in-place.
        # Cleared on every phase transition (see _transition).
        "next_eligible_at": None,
        # Tail of the most recently failed agent's captured output, injected
        # into that same phase's next retry prompt (see _prior_failure_note).
        "last_failure": None,
        # Verifier's own stdout/stderr from the last _validate() run, folded
        # into TRIAGE's prompt so it doesn't have to re-derive the failure.
        "last_verify_output": None,
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


def _research_summary_file(slug: str) -> Path:
    return _museum_data_dir(slug) / _RESEARCH_SUMMARY_FILE


def _note(ms: Dict, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ms["notes"].append(f"{ts} {msg}")


def _parse_iso_epoch(iso_str: Optional[str]) -> Optional[float]:
    """Parse an ISO-8601 timestamp (as stored in ms["started_at"]) to epoch
    seconds, or None if unset/unparseable. Used for wall-clock elapsed-time
    checks (startup grace, etc.) that must survive driver restarts — unlike
    time.monotonic(), which resets on every process start."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _transition(ms: Dict, new_phase: str, reason: str = "") -> None:
    log.info("[%s] %s → %s  %s", ms["slug"], ms["phase"], new_phase, reason)
    ms["phase"] = new_phase
    ms["next_eligible_at"] = None  # any phase-scoped backoff (e.g. VALIDATE INCONCLUSIVE) is now stale
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


# Triage buckets for NEEDS_HUMAN escalations, so a glance at the Telegram
# notification (via openclaw tailing NEEDS_HUMAN.md) says whether this needs
# a laptop or can be poked from a phone, and whether it's actually blocking
# anything or just sitting there safely.
#   can-wait     — blocked on something outside the repo (a credential, a
#                  manual step); nothing is running, zero cost to ignore.
#   quick-remote — a bounded yes/no decision (review a small diff, free disk
#                  space); a couple minutes, doable from a phone.
#   needs-focus  — genuinely needs someone to read logs/code and reason about
#                  root cause; do this at a computer, not urgent.
_ESCALATION_CATEGORIES = {
    "can-wait": ("CAN WAIT", "Blocked on something outside the codebase (credential, manual step, a decision only you can make). Nothing is running — safe to leave as long as you like."),
    "quick-remote": ("QUICK / REMOTE", "Should be a couple-minute yes/no call — doable from your phone. No deep investigation needed."),
    "needs-focus": ("NEEDS FOCUS", "Needs someone to actually read the logs/diff and reason about it. Not urgent, but better done at a computer."),
}


def escalate(ms: Dict, reason: str, extra: str = "", category: str = "needs-focus") -> None:
    slug = ms["slug"]
    phase = ms["phase"]
    notes_tail = "\n".join(ms["notes"][-10:])
    tag, hint = _ESCALATION_CATEGORIES.get(category, _ESCALATION_CATEGORIES["needs-focus"])
    body = (
        f"[{tag}] {hint}\n\n"
        f"Museum: {slug}\nPhase: {phase}\nReason: {reason}\n\n"
        f"{extra}\n\nRecent notes:\n{notes_tail}\n\n"
        f"To resume: python scripts/loop.py resume {slug}\n"
        f"To skip:   python scripts/loop.py skip {slug}"
    )
    notify(f"ArtServe NEEDS_HUMAN [{tag}] — {slug}", body)
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
# Agent output capture and retry feedback
#
# `claude -p` agents used to run with stdout/stderr inherited (nothing kept),
# so a failed attempt gave the retry nothing but a byte-identical prompt — the
# second attempt would frequently repeat the exact same dead end. Each agent
# invocation is now teed to a per-attempt log file, and a failed attempt's
# tail is folded into the next retry's prompt.
# ---------------------------------------------------------------------------

_FAILURE_TAIL_CHARS = 2000


def _agent_log_file(slug: str, phase: str, attempt: int) -> Path:
    log_dir = _museum_data_dir(slug) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{phase.lower()}-{attempt}.log"


def _record_agent_failure(ms: Dict, phase: str, attempt: int, log_file: Path) -> None:
    """Save the tail of a failed agent's captured output so the next retry's
    prompt can reference it instead of repeating the same dead end."""
    try:
        text = log_file.read_text(errors="replace")
    except Exception:
        text = ""
    ms["last_failure"] = {"phase": phase, "attempt": attempt, "tail": text[-_FAILURE_TAIL_CHARS:]}


def _prior_failure_note(ms: Dict, phase: str) -> str:
    """Prompt snippet describing the previous failed attempt at this same
    phase, or "" if there isn't one (first attempt, log file empty, or the
    last recorded failure was a different phase)."""
    lf = ms.get("last_failure")
    if not lf or lf.get("phase") != phase:
        return ""
    tail = lf.get("tail", "").strip()
    if not tail:
        return ""
    return (
        f"\nA previous {phase} attempt (#{lf.get('attempt')}) failed. Its final output was:\n"
        f"---\n{tail}\n---\n"
        f"Avoid repeating whatever approach led to that failure.\n\n"
    )


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
# Stall threshold & rate-limit backoff
# ---------------------------------------------------------------------------

def _museum_base_rate(slug: str) -> float:
    """Per-item rate-limit seconds for `slug`, read from this museum's own
    Settings field (e.g. settings.cma_rate_limit) instead of a hand-maintained
    table — a prior version of this table was a static dict that silently
    went stale as museums were added (tepapa alone was off by 25x: hardcoded
    at 5.0s/item vs. the real 0.2s/item default) and never covered
    belvedere/lacma/harvard/getty/kunstmuseumbasel at all, which meant their
    stall thresholds were always computed off the wrong number."""
    try:
        from src.config import settings
        settings.initialize_paths(PROJECT_ROOT)
        return float(getattr(settings, f"{slug}_rate_limit"))
    except Exception:
        return 2.0  # unknown museum or missing field — conservative fallback


def _museum_rate_limit_env_var(slug: str) -> str:
    """Env var name this museum's client actually reads for its rate limit.

    NOT necessarily what config.py's `Field(..., env="...")` claims: pydantic
    v2 (pydantic-settings 2.x) silently ignores that kwarg — it's a pydantic
    v1 relic (see the PydanticDeprecatedSince20 warning on every Settings
    field that still sets it) — and resolves the env var from
    SCREAMING_SNAKE_CASE of the field name instead. Verified empirically:
    CMA's field is `cma_rate_limit`, and only `CMA_RATE_LIMIT` has any effect
    on it — `CLEVELAND_RATE_LIMIT`, what its `env=` kwarg names, does nothing.
    This is also why the old flat `RATE_LIMIT_DELAY` env var this function
    replaces never worked for rung-2 backoff: no museum client reads it or
    `settings.rate_limit_delay` for its per-item pacing — each museum reads
    its own `{slug}_rate_limit` field, and that field's real env var is
    always `{SLUG}_RATE_LIMIT`, matched here regardless of what a museum's
    own `env=` kwarg happens to claim."""
    return f"{slug.upper()}_RATE_LIMIT"


def _stall_threshold(slug: str, ms: Dict) -> int:
    """Compute adaptive stall threshold in seconds."""
    rate = ms.get("rate_override") or _museum_base_rate(slug)
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

    prior_failure_note = _prior_failure_note(ms, "RESEARCH")

    prompt = (
        f"{prior_failure_note}"
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
        f"  7. Authentication requirements (if any) — be exact: does the API require a key/token\n"
        f"     at all, and if so, is it self-service (an instant API key from a developer portal)\n"
        f"     or does it require a human to fill out a registration form / request access by email?\n"
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
        f"Cite the exact endpoint URLs and field names you found.\n\n"
        f"ALSO write data/{slug}/research_summary.json (a plain directory reachable from your cwd,\n"
        f"not a scope violation — it isn't docs/{slug}.md, but it's the designated machine-readable\n"
        f"companion to it) with exactly these fields, reflecting your answer to item 7 above:\n"
        f'  {{"auth_required": true|false,\n'
        f'   "auth_notes": "one or two sentences — what kind of credential, and how a human gets one",\n'
        f'   "signup_url": "the exact URL to request/register for a key, or null if auth_required is false",\n'
        f'   "suggested_env_var": "SCREAMING_SNAKE_CASE env var name matching this project\'s convention\n'
        f'                         (e.g. HARVARD_API_KEY), or null if auth_required is false"}}\n'
        f"This lets the driver check for a working credential before wasting a BUILD/TRIAGE cycle on\n"
        f"a museum that's blocked on a human registering for API access — get it right.\n\n"
        f"TOOLING NOTE: WebFetch converts pages to markdown, which silently drops embedded\n"
        f"<script> JSON. Many museum 'collection online' sites have no documented REST API at\n"
        f"all — they're client-rendered apps (often Next.js) whose object-detail pages are\n"
        f"still server-rendered with the full record embedded as JSON in a\n"
        f"<script id=\"__NEXT_DATA__\" type=\"application/json\"> tag, which WebFetch cannot see\n"
        f"but a raw fetch can. If WebSearch/WebFetch turn up no formal API docs, or WebFetch\n"
        f"keeps returning thin/uninformative summaries of what looks like a data-heavy page,\n"
        f"use Bash (curl) to pull the RAW html yourself and grep/parse it instead of giving up:\n"
        f"  - Look for __NEXT_DATA__ (or similar framework hydration data) in the raw HTML of\n"
        f"    an object detail page — it's often the whole record as JSON, no auth needed.\n"
        f"  - If the listing page's HTML looks empty (data fetched client-side after load),\n"
        f"    check /_next/static/<buildId>/_buildManifest.js for the real page route map —\n"
        f"    e.g. it revealed /{{lng}}/collection/item/{{id}} for kunstmuseum-basel when the\n"
        f"    guessed /object/{{id}} route 404'd. See docs/kunstmuseum-basel.md for a full\n"
        f"    worked example of this pattern (SSR JSON extraction, no listing API found,\n"
        f"    hotlink-protected images requiring a Referer header, per-record rights field).\n"
        f"  - Image URLs are sometimes hotlink-protected (403 with no Referer) — retry with\n"
        f"    `curl -e <a page URL on the same site>` before concluding an image is inaccessible.\n"
        f"  - You may use Bash for read-only investigation (curl, grep, python for JSON parsing)\n"
        f"    only. Do not use it to modify anything outside docs/{slug}.md.\n"
    )

    cmd = [
        "claude", "-p", prompt,
        "--allowedTools", "WebSearch,WebFetch,Read,Write,Bash",
        "--permission-mode", "bypassPermissions",
        "--max-turns", "35",
        "--output-format", "text",
    ]

    ms["attempts"]["research"] += 1
    attempt = ms["attempts"]["research"]
    log.info("[%s] Starting RESEARCH agent (attempt %d)", slug, attempt)
    log_file = _agent_log_file(slug, "research", attempt)

    try:
        with log_file.open("w") as fh:
            proc = subprocess.Popen(cmd, cwd=run_cwd, stdout=fh, stderr=subprocess.STDOUT)
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
        _record_agent_failure(ms, "RESEARCH", attempt, log_file)
        return False

    # Audit changed files against the isolated worktree — RESEARCH now has Bash
    # (for raw curl investigation) so it can in principle write/edit anywhere;
    # it's only ever supposed to touch its own doc.
    all_ok, changed, forbidden = (
        _allowed_files_changed(slug, cwd=run_cwd, allowed_prefixes=(f"docs/{slug}.md",))
        if wt_path else (True, [], [])
    )
    if not all_ok:
        changed, ok = _handle_forbidden_files(slug, ms, "RESEARCH", run_cwd, wt_path, changed, forbidden)
        if not ok:
            _record_agent_failure(ms, "RESEARCH", attempt, log_file)
            return False

    if wt_path:
        _sync_worktree_changes(wt_path, changed)
        _remove_agent_worktree(wt_path)

    if rc != 0:
        log.error("[%s] RESEARCH agent exited with code %d", slug, rc)
        _record_agent_failure(ms, "RESEARCH", attempt, log_file)
        return False

    if not doc_file.exists():
        log.error("[%s] RESEARCH completed but docs/%s.md not written", slug, slug)
        _record_agent_failure(ms, "RESEARCH", attempt, log_file)
        return False

    log.info("[%s] RESEARCH done — docs/%s.md written", slug, slug)
    return True


def _check_credential_gate(slug: str, ms: Dict) -> bool:
    """After a successful RESEARCH, check research_summary.json for a
    required credential that isn't in the environment yet — and if so,
    escalate immediately (can-wait) instead of letting BUILD and then TRIAGE
    each burn a cycle rediscovering the same "needs a human to register for
    an API key" conclusion RESEARCH already reached (this is exactly what
    happened with harvard — see NEEDS_HUMAN.md history).

    Returns True if it's safe to proceed to BUILD; False if it escalated
    (caller should not transition)."""
    summary_file = _research_summary_file(slug)
    if not summary_file.exists():
        log.warning("[%s] RESEARCH did not write research_summary.json — skipping credential gate", slug)
        return True

    try:
        summary = json.loads(summary_file.read_text())
    except Exception as exc:
        log.warning("[%s] Could not parse research_summary.json: %s — skipping credential gate", slug, exc)
        return True

    if not summary.get("auth_required"):
        return True

    env_var = summary.get("suggested_env_var")
    if env_var and os.environ.get(env_var):
        log.info("[%s] Credential gate: %s is set — proceeding to BUILD", slug, env_var)
        return True

    auth_notes = summary.get("auth_notes", "")
    signup_url = summary.get("signup_url") or "(not provided — see research doc)"
    env_hint = f" and set {env_var} in .env" if env_var else ""
    escalate(
        ms,
        f"{slug} requires a credential that isn't set" + (f" ({env_var})" if env_var else ""),
        extra=(
            f"{auth_notes}\n\n"
            f"Sign up at: {signup_url}\n"
            f"Then register for the key{env_hint}, then run:\n"
            f"  python scripts/loop.py resume {slug}"
        ),
        category="can-wait",
    )
    return False


# ---------------------------------------------------------------------------
# Phase: BUILD
# ---------------------------------------------------------------------------

def _classify_forbidden_files(slug: str, cwd: Path, forbidden: List[str]) -> tuple:
    """Split `forbidden` into (infra, genuine) — each a list of (rel_path, wt_diff)
    tuples. A file lands in `infra` when its worktree diff-from-HEAD is byte-identical
    to PROJECT_ROOT's own current uncommitted diff for the same path: the signature of
    the worktree having been branched from a stale/dirty HEAD (see the worktree-isolation
    note above) rather than the agent genuinely editing an out-of-scope file. Best-effort:
    a diagnostic failure classifies the file as genuine — never let it silently swallow
    a real escalation."""
    infra: List[tuple] = []
    genuine: List[tuple] = []
    for rel in forbidden:
        try:
            _, wt_diff, _ = _run_cmd_capture(["git", "diff", "HEAD", "--", rel], cwd=cwd)
            _, root_diff, _ = _run_cmd_capture(["git", "diff", "HEAD", "--", rel], cwd=PROJECT_ROOT)
            if wt_diff.strip() and wt_diff == root_diff:
                infra.append((rel, wt_diff))
            else:
                genuine.append((rel, wt_diff))
        except Exception as exc:
            log.warning("[%s]   %s: diagnostic failed: %s", slug, rel, exc)
            genuine.append((rel, ""))
    return infra, genuine


def _handle_forbidden_files(
    slug: str, ms: Dict, phase: str, run_cwd: Path, wt_path: Optional[Path],
    changed: List[str], forbidden: List[str],
) -> tuple:
    """Given a failed _allowed_files_changed() audit, filter out infra false
    positives (see _classify_forbidden_files) and escalate only if a genuine
    out-of-scope edit remains.

    Returns (changed, ok). ok=True means the caller should proceed as if the
    audit had passed — the infra files are dropped from `changed` so they are
    never synced back to PROJECT_ROOT (copying them would be a no-op anyway,
    since their diff already matches, but dropping is the correct intent).
    ok=False means a genuine violation was found and escalate() was already
    called; the caller should return failure without syncing."""
    infra, genuine = _classify_forbidden_files(slug, run_cwd, forbidden)

    if infra:
        infra_paths = [rel for rel, _ in infra]
        log.warning(
            "[%s] %s: %d forbidden file(s) match PROJECT_ROOT's own uncommitted "
            "diff exactly — treating as stale/dirty-HEAD false positives, not "
            "agent edits, and not escalating: %s",
            slug, phase, len(infra_paths), infra_paths,
        )
        changed = [f for f in changed if f not in infra_paths]

    if not genuine:
        return changed, True

    genuine_paths = [rel for rel, _ in genuine]
    log.error("[%s] %s touched forbidden files: %s", slug, phase, genuine_paths)
    for rel, wt_diff in genuine:
        snippet = "\n".join(wt_diff.splitlines()[:20])
        log.error("[%s]   %s: worktree-only change:\n%s", slug, rel, snippet)
    if wt_path:
        log.error("[%s] Worktree left in place for inspection: %s", slug, wt_path)
    escalate(ms, f"{phase} agent touched forbidden files: {genuine_paths}", category="quick-remote")
    return changed, False


def _allowed_files_changed(slug: str, cwd: Path = PROJECT_ROOT, allowed_prefixes: Optional[tuple] = None) -> tuple:
    """Returns (all_allowed, changed_files, forbidden_files). all_allowed=True means
    no out-of-scope files. `cwd` should be the agent's own worktree — diffing
    PROJECT_ROOT directly would also pick up unrelated concurrent edits sitting
    in the main checkout (see the worktree-isolation note above). Defaults to the
    BUILD/TRIAGE code-scope prefixes; pass `allowed_prefixes` to override (e.g. for
    RESEARCH, which should only ever touch its own doc)."""
    changed = _changed_files_in(cwd)
    if allowed_prefixes is None:
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

    prior_failure_note = _prior_failure_note(ms, "BUILD")

    prompt = (
        f"{prior_failure_note}"
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
    attempt = ms["attempts"]["build"]
    log.info("[%s] Starting BUILD agent (attempt %d)", slug, attempt)
    log_file = _agent_log_file(slug, "build", attempt)

    try:
        with log_file.open("w") as fh:
            proc = subprocess.Popen(cmd, cwd=run_cwd, stdout=fh, stderr=subprocess.STDOUT)
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
        _record_agent_failure(ms, "BUILD", attempt, log_file)
        return False

    # Audit changed files against the isolated worktree — never PROJECT_ROOT
    # directly, which could be dirty from unrelated concurrent work
    all_ok, changed, forbidden = _allowed_files_changed(slug, cwd=run_cwd)
    if not all_ok:
        changed, ok = _handle_forbidden_files(slug, ms, "BUILD", run_cwd, wt_path, changed, forbidden)
        if not ok:
            _record_agent_failure(ms, "BUILD", attempt, log_file)
            return False

    if wt_path:
        _sync_worktree_changes(wt_path, changed)
        _remove_agent_worktree(wt_path)

    museum_file = PROJECT_ROOT / "src" / "museums" / f"{slug}.py"
    if not museum_file.exists():
        log.error("[%s] BUILD did not create src/museums/%s.py", slug, slug)
        _record_agent_failure(ms, "BUILD", attempt, log_file)
        return False

    if rc != 0:
        log.error("[%s] BUILD agent exited with code %d", slug, rc)
        _record_agent_failure(ms, "BUILD", attempt, log_file)
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

    # Kept for TRIAGE's prompt (see _triage) — the verifier's own diagnosis of
    # why VALIDATE failed is more informative than the bare exit code.
    ms["last_verify_output"] = {
        "exit_code": rc,
        "stdout": stdout[-2000:] if stdout else "",
        "stderr": stderr[-1000:] if stderr else "",
    }

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


# Popen objects for downloads started by *this* driver process, keyed by
# slug. Nothing but loop.py itself writes run_exit.json — main.py doesn't —
# so the only reliable way to learn a download's exit code is to hold its
# Popen and poll() it ourselves. A pid reattached from a prior driver
# incarnation (after a restart) never had a Popen object in this process to
# begin with; that case falls back to _read_run_exit, which will be empty
# unless this same process happened to observe and record the exit before
# restarting — a pre-existing limitation, not something this refactor
# introduces or attempts to fix.
_ACTIVE_DOWNLOADS: Dict[str, subprocess.Popen] = {}


def _terminate_download(slug: str, pid: int, proc: Optional[subprocess.Popen]) -> None:
    """Send SIGTERM and reap the child. Uses the held Popen object when this
    driver process started it (bounded wait, mirrors subprocess.Popen.wait
    semantics); falls back to a bare os.kill for a pid reattached from a
    prior driver incarnation, which has no Popen handle to wait on."""
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            pass
        _ACTIVE_DOWNLOADS.pop(slug, None)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _count_active_runs(state: Dict) -> int:
    """Number of museums currently mid-download (run_pid set) — used to
    enforce MAX_CONCURRENT_RUNS. Scans `state` directly so it reflects any
    downloads already started earlier in the same driver tick."""
    return sum(1 for ms in state.get("museums", {}).values() if ms.get("run_pid"))


def _start_download(slug: str, ms: Dict, state: Dict) -> None:
    """Launch a new download subprocess in the background and return
    immediately — does not poll or block. Health-checking happens on later
    driver ticks via _run_health_check."""
    if not _disk_ok():
        _handle_run_outcome(slug, ms, state, "DISK_FULL")
        return

    env = os.environ.copy()
    if ms.get("rate_override"):
        env[_museum_rate_limit_env_var(slug)] = str(ms["rate_override"])

    cmd = _build_run_cmd(slug, ms)
    # Remove old run exit file so we don't read a stale one
    _run_exit_file(slug).unlink(missing_ok=True)

    log.info("[%s] Starting download: %s", slug, " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=env)
    _ACTIVE_DOWNLOADS[slug] = proc
    ms["run_pid"] = proc.pid
    ms["started_at"] = datetime.now(timezone.utc).isoformat()
    ms["last_processed_count"] = _read_processed_count(slug)
    ms["last_progress_mtime"] = time.time()
    ms["attempts"]["restart"] += 1

    stall_threshold = _stall_threshold(slug, ms)
    log.info(
        "[%s] Running in background (driver tick=%ds), stall threshold=%ds, startup grace=%ds",
        slug, POLL_BASE_SECONDS, stall_threshold, STARTUP_GRACE_SECONDS,
    )


def _run_health_check(slug: str, ms: Dict, state: Dict) -> None:
    """Non-blocking health check for a museum in RUN with an active run_pid:
    resolve and apply the outcome via _handle_run_outcome if the download
    concluded (or must be terminated) this tick, otherwise leave it running.

    Replaces the old _check_prior_run/_poll_existing_pid split — there is no
    longer a distinction between "a Popen object we're holding" and "a bare
    pid from a previous driver incarnation": every RUN museum is tracked the
    same way (pid + state file), whether the download started this driver
    process or an earlier one.
    """
    pid = ms["run_pid"]

    # If this driver process started the download, we hold its Popen object —
    # use it to capture the exit code reliably and persist it (see the
    # _ACTIVE_DOWNLOADS docstring). Reattaching to a pid from a prior driver
    # incarnation has no such handle.
    proc = _ACTIVE_DOWNLOADS.get(slug)
    if proc is not None and proc.poll() is not None:
        _write_run_exit(slug, proc.returncode)
        del _ACTIVE_DOWNLOADS[slug]

    if not _pid_alive(pid):
        exit_code = _read_run_exit(slug)
        ms["run_pid"] = None
        if exit_code is None:
            log.warning("[%s] Child pid=%d gone with no exit marker — treating as CRASH", slug, pid)
            _handle_run_outcome(slug, ms, state, "CRASH")
            return
        if exit_code == 0:
            summary = _read_run_summary(slug)
            if summary and summary.get("reached_end_of_collection") and summary.get("total_processed", 0) > 0:
                _handle_run_outcome(slug, ms, state, "DONE")
                return
            log.warning("[%s] Exit 0 but run_summary missing end-of-collection flag", slug)
            _handle_run_outcome(slug, ms, state, "CRASH")
            return
        log.error("[%s] Child exited with code %d", slug, exit_code)
        _handle_run_outcome(slug, ms, state, "CRASH")
        return

    if not _disk_ok():
        _terminate_download(slug, pid, proc)
        ms["run_pid"] = None
        _handle_run_outcome(slug, ms, state, "DISK_FULL")
        return

    # Stall detection — skip during startup grace, measured from the
    # wall-clock `started_at` timestamp (survives driver restarts, unlike a
    # time.monotonic() deadline computed once inside a blocking loop).
    started_epoch = _parse_iso_epoch(ms.get("started_at"))
    in_startup_grace = started_epoch is not None and (time.time() - started_epoch) < STARTUP_GRACE_SECONDS
    if in_startup_grace:
        return

    stall_threshold = _stall_threshold(slug, ms)
    count_now = _read_processed_count(slug)
    pf = _progress_file(slug)
    mtime_now = pf.stat().st_mtime if pf.exists() else (ms.get("last_progress_mtime") or time.time())

    progress_advanced = count_now > ms.get("last_processed_count", 0)
    time_since_progress = time.time() - (mtime_now or time.time())

    if progress_advanced:
        ms["last_processed_count"] = count_now
        ms["last_progress_mtime"] = mtime_now

    if not progress_advanced and time_since_progress > stall_threshold:
        log.warning(
            "[%s] STALL detected: %d items, %.1fh since last progress (threshold %.1fh)",
            slug, count_now, time_since_progress / 3600, stall_threshold / 3600,
        )
        _terminate_download(slug, pid, proc)
        ms["run_pid"] = None
        _handle_run_outcome(slug, ms, state, "STALL")


def _tick_run(slug: str, ms: Dict, state: Dict) -> None:
    """One non-blocking iteration of the RUN phase for `slug`: health-check an
    active download, or start a fresh one if a MAX_CONCURRENT_RUNS slot is
    free. Never blocks or sleeps — the driver's own tick interval
    (POLL_BASE_SECONDS) provides the polling cadence, which is what lets other
    museums keep advancing through RESEARCH/BUILD/VALIDATE/TRIAGE while this
    one downloads in the background."""
    if ms.get("run_pid"):
        _run_health_check(slug, ms, state)
        _save_state(state)
        return

    if _count_active_runs(state) >= MAX_CONCURRENT_RUNS:
        return  # waiting for a slot; nothing new to log every tick

    _start_download(slug, ms, state)
    _save_state(state)


def _handle_run_outcome(slug: str, ms: Dict, state: Dict, outcome: str) -> None:
    """Apply restart ladder based on run outcome."""
    if outcome == "DONE":
        _transition(ms, "DONE", "clean exit + end-of-collection confirmed")
        notify(f"ArtServe DONE — {slug}", f"{slug} finished downloading (processed {ms.get('last_processed_count', 0)} items).")
        return

    if outcome == "DISK_FULL":
        escalate(ms, "Disk space below minimum threshold — free space and resume", extra="", category="quick-remote")
        return

    # CRASH or STALL — apply restart ladder
    restart_count = ms["attempts"]["restart"]
    triage_count = ms["attempts"]["triage"]

    rung_1_exhausted = restart_count > MAX_RESTART_ATTEMPTS
    rung_2_exhausted = restart_count > MAX_RESTART_ATTEMPTS + MAX_BACKOFF_ATTEMPTS

    if not rung_1_exhausted:
        log.info("[%s] %s — rung 1 restart (attempt %d/%d)", slug, outcome, restart_count, MAX_RESTART_ATTEMPTS)
        _note(ms, f"Rung 1 restart after {outcome}")
        # Stay in RUN phase; next tick's _tick_run will start a fresh download
    elif not rung_2_exhausted:
        # Rung 2: increase rate delay
        current = ms.get("rate_override") or _museum_base_rate(slug)
        ms["rate_override"] = round(current * RATE_BACKOFF_MULTIPLIER, 2)
        log.info(
            "[%s] %s — rung 2: rate backoff to %.1fs/item (%s)",
            slug, outcome, ms["rate_override"], _museum_rate_limit_env_var(slug),
        )
        _note(ms, f"Rung 2 rate backoff to {ms['rate_override']}s after {outcome}")
    elif triage_count < MAX_TRIAGE_ATTEMPTS:
        _transition(ms, "TRIAGE", f"after {outcome} ({restart_count} restarts)")
    else:
        escalate(ms, f"Exhausted {MAX_TRIAGE_ATTEMPTS} triage attempts after {outcome}", category="needs-focus")


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
    prior_failure_note = _prior_failure_note(ms, "TRIAGE")

    verify = ms.get("last_verify_output") or {}
    verify_section = ""
    if verify.get("stdout") or verify.get("stderr"):
        verify_section = (
            f"Verifier output (exit {verify.get('exit_code')}):\n"
            f"stdout:\n{verify.get('stdout', '')}\n"
        )
        if verify.get("stderr"):
            verify_section += f"stderr:\n{verify.get('stderr')}\n"
        verify_section += "\n"

    prompt = (
        f"{prior_failure_note}"
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
        f"{verify_section}"
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
    attempt = ms["attempts"]["triage"]
    log.info("[%s] Starting TRIAGE agent (attempt %d)", slug, attempt)
    log_file = _agent_log_file(slug, "triage", attempt)

    try:
        with log_file.open("w") as fh:
            proc = subprocess.Popen(cmd, cwd=run_cwd, stdout=fh, stderr=subprocess.STDOUT)
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
        _record_agent_failure(ms, "TRIAGE", attempt, log_file)
        return False

    # Audit changed files against the isolated worktree — never PROJECT_ROOT
    # directly, which could be dirty from unrelated concurrent work
    all_ok, changed, forbidden = _allowed_files_changed(slug, cwd=run_cwd)
    if not all_ok:
        changed, ok = _handle_forbidden_files(slug, ms, "TRIAGE", run_cwd, wt_path, changed, forbidden)
        if not ok:
            _record_agent_failure(ms, "TRIAGE", attempt, log_file)
            return False

    if wt_path:
        _sync_worktree_changes(wt_path, changed)
        _remove_agent_worktree(wt_path)

    # Read verdict
    verdict_file = _triage_verdict_file(slug)
    if not verdict_file.exists():
        log.error("[%s] TRIAGE did not write triage_verdict.json", slug)
        _record_agent_failure(ms, "TRIAGE", attempt, log_file)
        return False

    try:
        verdict = json.loads(verdict_file.read_text())
    except Exception as exc:
        log.error("[%s] Could not parse triage_verdict.json: %s", slug, exc)
        _record_agent_failure(ms, "TRIAGE", attempt, log_file)
        return False

    _note(ms, f"TRIAGE verdict={verdict.get('verdict')} hypothesis={verdict.get('hypothesis','')[:100]}")

    if verdict.get("verdict") == "cannot_fix":
        escalate(ms, f"Triage cannot_fix: {verdict.get('hypothesis','')}", extra=str(verdict), category="can-wait")
        return False

    # Agent claims fixed — verify independently
    verifier_status = _validate(slug, ms)
    if verifier_status != "PASS":
        log.error("[%s] Triage claimed fixed but verifier says %s", slug, verifier_status)
        _record_agent_failure(ms, "TRIAGE", attempt, log_file)
        return False

    log.info("[%s] TRIAGE fix confirmed by verifier", slug)
    return True


# ---------------------------------------------------------------------------
# Main driver loop
# ---------------------------------------------------------------------------

def _pick_active(state: Dict) -> Optional[str]:
    """Return slug of the first museum that needs a synchronous state-machine
    step (RESEARCH/BUILD/VALIDATE/TRIAGE). Museums in RUN are deliberately
    excluded — the driver's per-tick health-check pass (_tick_run) advances
    those independently and non-blockingly, so a multi-day download doesn't
    stall the rest of the queue. A museum with a future `next_eligible_at`
    (e.g. VALIDATE INCONCLUSIVE backoff) is skipped without blocking the
    driver — other museums keep advancing until its backoff expires."""
    now = time.time()
    for slug in state.get("queue", []):
        ms = state["museums"].get(slug, {})
        phase = ms.get("phase")
        if phase in ("DONE", "NEEDS_HUMAN", "RUN") or ms.get("needs_human"):
            continue
        next_eligible = ms.get("next_eligible_at")
        if next_eligible and now < next_eligible:
            continue
        return slug
    return None


def step(slug: str, state: Dict) -> None:
    """Execute one state-machine step for slug."""
    ms = state["museums"][slug]
    phase = ms["phase"]

    if phase == "RESEARCH":
        ok = _research(slug, ms, state)
        if ok:
            if _check_credential_gate(slug, ms):
                _transition(ms, "BUILD", "research doc written")
            # else: _check_credential_gate already escalated (can-wait)
        elif not ms.get("needs_human"):
            # _research() already escalated internally (e.g. forbidden files) if
            # needs_human is set — avoid a second escalation/ntfy push for the
            # same failure.
            if ms["attempts"]["research"] >= 2:
                escalate(ms, "RESEARCH agent failed twice", category="needs-focus")
            else:
                _note(ms, "RESEARCH attempt failed; will retry")

    elif phase == "BUILD":
        ok = _build(slug, ms, state)
        if ok:
            _transition(ms, "VALIDATE", "museum file created")
        elif not ms.get("needs_human"):
            if ms["attempts"]["build"] >= 2:
                escalate(ms, "BUILD agent failed twice", category="needs-focus")
            else:
                _note(ms, "BUILD attempt failed; will retry")

    elif phase == "VALIDATE":
        ms["attempts"]["validate"] += 1
        result = _validate(slug, ms)
        if result == "PASS":
            ms["attempts"]["validate_inconclusive"] = 0
            _transition(ms, "RUN", "verifier PASS")
        elif result == "INCONCLUSIVE":
            count = ms["attempts"].get("validate_inconclusive", 0) + 1
            ms["attempts"]["validate_inconclusive"] = count
            if count >= MAX_VALIDATE_INCONCLUSIVE:
                escalate(
                    ms,
                    f"Verifier INCONCLUSIVE {count} times in a row — network or API outage?",
                    category="can-wait",
                )
            else:
                log.warning("[%s] Verifier INCONCLUSIVE (%d/%d) — will retry", slug, count, MAX_VALIDATE_INCONCLUSIVE)
                _note(ms, f"Verifier INCONCLUSIVE ({count}/{MAX_VALIDATE_INCONCLUSIVE}, network?)")
                # Non-blocking backoff — other museums keep advancing in the
                # meantime; see _pick_active.
                ms["next_eligible_at"] = time.time() + 300
        else:
            ms["attempts"]["validate_inconclusive"] = 0
            log.error("[%s] Verifier FAIL — going to TRIAGE", slug)
            _transition(ms, "TRIAGE", "verifier FAIL after BUILD")

    elif phase == "RUN":
        # Non-blocking: health-check an active download or start a fresh one
        # (subject to MAX_CONCURRENT_RUNS). See _tick_run.
        _tick_run(slug, ms, state)

    elif phase == "TRIAGE":
        fixed = _triage(slug, ms, state)
        if fixed:
            # Reset restart counter so it gets fresh rung-1 attempts
            ms["attempts"]["restart"] = 0
            _transition(ms, "RUN", "triage fix verified")
        elif not ms.get("needs_human"):
            # _triage() already escalated internally (forbidden files, or a
            # cannot_fix verdict) if needs_human is set.
            if ms["attempts"]["triage"] >= MAX_TRIAGE_ATTEMPTS:
                escalate(ms, f"Exhausted {MAX_TRIAGE_ATTEMPTS} triage attempts", category="needs-focus")

    elif phase in ("DONE", "NEEDS_HUMAN"):
        pass  # nothing to do

    _save_state(state)


_DIRTY_TREE_WARN_PREFIXES = ("src/", "main.py", "scripts/", "docs/", "tests/")


def _warn_if_dirty_tree() -> None:
    """Log a one-time warning if PROJECT_ROOT has uncommitted changes under
    paths agents read/write from — this is exactly the condition that produces
    the identical-diff false positives _handle_forbidden_files filters out
    downstream. Not an escalation: the driver still runs fine, but every
    RESEARCH/BUILD/TRIAGE agent worktree branches from this dirty HEAD until
    it's committed or discarded."""
    rc, out, _ = _run_cmd_capture(["git", "status", "--porcelain"], cwd=PROJECT_ROOT)
    if rc != 0:
        return
    dirty = [
        line[3:] for line in out.splitlines()
        if line[3:].startswith(_DIRTY_TREE_WARN_PREFIXES)
    ]
    if dirty:
        log.warning(
            "PROJECT_ROOT has %d uncommitted change(s) under agent-scoped paths: %s — "
            "agent worktrees branch from this dirty HEAD, which can produce spurious "
            "'touched forbidden files' escalations. Commit or discard before running.",
            len(dirty), dirty,
        )


def driver_loop() -> None:
    """Main loop — runs until all museums are DONE/NEEDS_HUMAN or SIGTERM.

    Each tick does two things:
      1. Health-check (or start, subject to MAX_CONCURRENT_RUNS) every museum
         currently in RUN. These calls never block — see _tick_run.
      2. Advance exactly one non-RUN museum through its next synchronous
         state-machine step (RESEARCH/BUILD/VALIDATE/TRIAGE invoke agents and
         share the repo/worktree infrastructure, so those stay serial).

    This is what lets a multi-day download run in the background while the
    rest of the queue keeps moving, instead of blocking the whole driver on
    one museum's RUN phase.
    """
    log.info("ArtServe museum loop driver starting")
    _warn_if_dirty_tree()
    while not _shutdown:
        state = _load_state()
        queue = state.get("queue", [])

        for slug in queue:
            ms = state["museums"].get(slug)
            if ms and ms.get("phase") == "RUN":
                _tick_run(slug, ms, state)
            if _shutdown:
                break

        if _shutdown:
            break

        active_slug = _pick_active(state)
        if active_slug is None:
            total = len(queue)
            done = sum(1 for s in queue
                       if state["museums"].get(s, {}).get("phase") in ("DONE", "NEEDS_HUMAN"))
            if total == 0:
                log.info("Queue is empty — nothing to do. Add museums with: loop.py add <slug>")
            elif done == total:
                log.info("All %d museums are DONE or NEEDS_HUMAN — loop complete", total)
                break
            # else: everything active this tick is in RUN (already
            # health-checked above) or backed off (next_eligible_at) —
            # nothing more to do until one of them concludes or is eligible.
        else:
            log.info("[%s] Active — phase=%s", active_slug, state["museums"][active_slug]["phase"])
            step(active_slug, state)

        if _shutdown:
            break
        time.sleep(POLL_BASE_SECONDS)

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
    ms["next_eligible_at"] = None  # clear any leftover non-blocking backoff
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
