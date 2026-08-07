# Plan: Semi-Autonomous Museum Downloader Loop (`scripts/loop.py`)

> **Deliverable of this task:** this design document. **No code is written in this task** — planning only.

## Context

ArtServe already has **10 fully-built, registered museum downloaders** (`config.py:225-316`,
`main.py:10-19`). The goal is a driver that adds *net-new* museums (beyond the 10) and then runs
them to completion with minimal human input, one at a time, via a persisted state machine:

```
RESEARCH -> BUILD -> VALIDATE -> RUN -> DONE
               ^                  |
               +---- TRIAGE <-----+
                        |
                        v
                   NEEDS_HUMAN
```

**Decisions locked with the user:**
- Primary job = **add net-new museums** (RESEARCH/BUILD are the high-value path).
- **Phase 0 seam refactor is blocking** — collapse duplication into `base.py` *before* automating.
- Agents run as **`claude -p` headless subprocesses** (rationale in §Agent Runtime).
- Runs on a **headless Raspberry Pi under systemd** (not macOS; no `osascript`).

**Two enabling defects found in the current code that the loop depends on fixing:**
- `main.py:353` `sys.exit(0)` is unconditional, and single-museum runs still go through
  `run_parallel_downloads` → `ThreadPoolExecutor`, which **swallows per-museum exceptions**
  (`main.py:59-61`). → The driver cannot distinguish crash from success. Must fix.
- `base.py:30` `requests_cache.install_cache` is **process-global** — a latent footgun for any
  in-process parallelism. Subprocess-per-museum sidesteps it; still worth making per-museum.

---

## 1. THE SEAM — what varies vs. what must collapse into `base.py` (Phase 0, blocking)

**Duplication to remove BEFORE automating** (an automated loop replicates whatever exists):

| Concern | Duplicated at | Fix |
|---|---|---|
| `process_image` (byte-identical: open→size→save JPEG q95) | `aic.py:189-219`, `met.py:320-345`, `cma.py:324-346` | Make it a **concrete** method on `MuseumImageProcessor` (`base.py:97-121`, today abstract). Museums override only if they truly differ. |
| `generate_filename` (differs only by ID prefix `AIC_`/`Met_`/`CMA_`) | `aic.py:221-229`, `met.py:347-357`, `cma.py:348-355` | Base method using `self.museum_info.code.upper()` as prefix. Delete overrides. |
| `_get_unprocessed_ids` (verbatim) | `met.py:146-159`, `cma.py:246-255` | Move to base client. |
| Object-ID cache: `_load_cached_object_ids` / `_save_object_ids_cache`, 24h TTL, corrupt-file unlink | `met.py:161-197`, `cma.py:257-299` | Consolidate into base client as `_load_cached_ids`/`_save_cached_ids` + a `cache_filename` attr. |
| Progress-tracker `get_state_dict`/`restore_state` boilerplate (same 4 base fields + extras) | `aic.py:253-273`, `met.py:383-413`, `cma.py:379-398`; note `CMAProgressState` doesn't even inherit `ProgressState` (`cma.py:359`) | Generic serialize/deserialize in `BaseProgressTracker` handling the base 4 fields + a declared `EXTRA_FIELDS = ["last_page", ...]`. Most museums then need **no** tracker subclass. |
| `isinstance(self.progress_tracker, XProgressTracker)` guards | `aic.py:119,137,153`, `met.py:237`, `cma.py:183,116,134` | Base tracker exposes no-op hooks `note_page/note_index/note_total`; clients call unconditionally. |

**What genuinely varies per museum (must stay per-museum — this is the real BUILD surface):**
- **Metadata factory** (JSON → `ArtworkMetadata`) in `schemas.py` — irreducible mapping.
- **Iteration strategy**: offset (aic/cma/smk), object-ID two-step (met), cursor (wellcome),
  in-memory CSV (nga), git-bucket + Playwright scrape (mia), POST offset (tepapa), page# (loc).
- **Image URL**: IIIF-from-id (aic/nga/smk), direct (met/cma), scraped (mia); some need size syntax.
- **Auth**: none / query-param key (rijks) / header key (tepapa).
- **Public-domain predicate**: server param vs local rule (`aic is_public_domain`, `cma share_license_status=='CC0'`, mia local whitelist).
- **Session customization** (`met._get_session`, `met.py:48-76`): browser UA, Referer, longer
  timeouts. Legitimate → expose a `_customize_session(session)` hook in base rather than a full override.

**After Phase 0, a net-new museum = (a) a factory, (b) `_iter_collection_impl`, (c) optional
`_customize_session`, (d) config+main registration, (e) usually no tracker.** Narrower surface =
less room for the BUILD agent to diverge.

**Constraint I'd push back on:** forcing every museum to ship a `ProgressTracker` subclass is
almost pure boilerplate. After Phase 0, default to the base tracker; only bespoke resume state
(e.g. AIC's `last_page`) justifies a subclass.

**Enabler fixes bundled into Phase 0:**
- `main.py`: single-museum path must **not** be swallowed by the parallel wrapper; propagate a real
  exit code (0 = all requested museums completed, non-zero = any failed). Remove unconditional
  `sys.exit(0)` (`main.py:353`).
- Write a machine-readable `data/<slug>/run_summary.json` at end of `download_collection`
  (reuse `_generate_summary_report`, `artwork_downloader.py:446`) so the driver reads counts
  without log parsing.
- Make `requests_cache` per-museum (path-scoped) rather than global (`base.py:30`).

---

## 2. VERIFIER SPEC — `scripts/verify_museum.py` (new; independent; un-gameable)

**Invocation:** `python scripts/verify_museum.py <slug>` → exit 0 = PASS, non-zero = FAIL,
exit code 2 = INCONCLUSIVE (network down — don't triage, retry later). Emits JSON to stdout:
`{status, checks:{...}, samples:[...]}`.

**Method:** builds the client exactly as `main.py:get_museum_config` does (so auth/params/headers
apply), iterates `client.iter_collection(**params)` for a bounded sample (first ~30 metadata, cap
5 min wall-clock, cap bytes), **without touching the DB or progress files** (fresh sample from the
live source — the agent cannot pre-seed fake "successful" rows).

**Assertions (minimum set that catches breakage without false-failing a healthy museum):**
- **A. Non-empty:** ≥ 10 metadata objects yielded within the cap. Zero → FAIL (broken pagination /
  wrong endpoint / wrong params).
- **B. Licence filter:** every sampled object `is_public_domain == True` (museum's open-access
  equivalent). Any leak → FAIL (**wrong licence filter**).
- **C. URL well-formed:** `primary_image_url` present, parses to http(s) with a host, not a
  placeholder (`example.com`, `data:`, empty).
- **D. Images are real (sample 10–15):** GET (stream), following redirects:
  - HTTP 200 after redirects.
  - **Magic bytes** check (`FFD8`=JPEG, `89504E47`=PNG, etc.) on first bytes, **and** PIL
    `Image.open` decodes on ≥2 samples → catches **error/placeholder HTML served as 200**.
  - Decoded dims ≥ 200×200 → catches 1×1 pixels and tiny placeholders returned as 200.
  - **Uniformity tell:** if all sampled images are byte-identical / same tiny size → FAIL
    (single placeholder served for everything).

**Per-museum special-handling table** (declarative dict in the verifier — relaxes **transport only**,
never correctness A–D):
- **Referer header** required by some CDNs (403 without it).
- **No HEAD support** (many IIIF/CDN) → always use ranged/stream GET, never HEAD.
- **IIIF size syntax**: verifier fetches whatever URL the factory produced; a bad size (`/full/full/`
  vs `/full/max/`) fails the fetch and is caught — no hardcoding of size in the verifier.
- Auth handled automatically via the shared build path.

**False-fail avoidance:** transient 503/timeout on a sample → retry / replace sample; if *all*
fetches are network errors → **INCONCLUSIVE (exit 2)**, driver waits & retries rather than
triaging (protects against flaky Pi wifi / source downtime).

**Un-gameable guarantees:**
- Verifier is **not** in any agent's editable path (enforced §4).
- Driver **`git checkout scripts/verify_museum.py`** before running it → the committed version
  always runs, even if an agent touched the working tree.
- Driver runs it in a **fresh subprocess after the agent has exited** — agent can't intercept.
- Thresholds are constants; the special-handling table can only loosen transport, not A–D.

---

## 3. LIVENESS DURING RUN (the hard part)

**Signals available today (zero agent tokens):**
- `data/<slug>/cache/processed_ids.json` — atomic write every `save_batch_size=100` items
  (`progress_tracker.py:128-137,166-169`). **File mtime + `len(processed_ids)` = the progress probe.**
- Subprocess **exit code** (after the `main.py` fix): 0 = clean, non-zero = crash.
- Log tail: `"Reached end of collection"` (`artwork_downloader.py:244`) = normal end;
  `"Too many consecutive errors"` (`:281`) = internal abort; traceback = crash.
- `data/<slug>/run_summary.json` (added in Phase 0) — final counts.

**Completion vs crash vs hang:**
- **Completion** = exit 0 **AND** `"Reached end of collection"` in log **AND** `processed_count > 0`.
  → DONE. (Exit 0 *without* the end line = suspicious silent early break → TRIAGE.)
- **Crash** = non-zero exit, or child gone with no exit marker and no completion line.
- **Silent hang** = child alive but `processed_ids.json` mtime + count unchanged for > stall threshold.

**Adaptive stall threshold** (a fixed timeout would false-alarm on cma and miss fast APIs):
- `expected_batch_interval ≈ save_batch_size(100) × per_item_seconds`, where `per_item_seconds`
  derives from the museum rate (+ image download). `stall_timeout = max(15 min, 4 × expected_batch_interval)`.
  - aic (1 s) → ~15 min. tepapa (5 s) → ~33 min. **cma (80 s) → ~9 h** (legitimately glacial;
    `MUSEUMS.md` says 3–4 days full collection — a fixed timeout would kill it).
- **Startup grace**: bulk/dump museums do heavy work *before the first write* — mia git clone
  (~1 GB), nga CSV load, met ID-list fetch (hundreds of thousands of IDs). Give a per-museum
  `startup_grace_seconds` (e.g. 60 min) before stall logic engages.
- Both knobs live in the driver's queue file (not agent-editable correctness).

**Restart ladder (cheapest first; attempt counts persisted):**
1. **Restart same command** — most hangs are transient (socket stall, Pi wifi blip). Resume is free
   (checkpointed; skips processed IDs). Try up to 2× before escalating. ~0 tokens, minutes.
2. **Restart with increased rate delay** — sustained 429/403 needs a slower *baseline*. No
   concurrency knob exists, so the lever is the env var (`<MUSEUM>_RATE_LIMIT` / `RATE_LIMIT_DELAY`),
   set on the child. (Met already has per-request 403 backoff, `met.py:258-272`; this is the
   baseline bump beyond that.) No agent.
3. **TRIAGE agent** — only when restart + backoff don't restore progress, or a non-zero exit shows a
   code/schema traceback (not network). Costs tokens (§4).
4. **NEEDS_HUMAN** — after 3 triage attempts / triage says can't-fix / verifier hash tampered /
   hard licence failure triage couldn't resolve.

**Poll interval & persistence:**
- Poll every `min(stall_timeout/4, 5 min)` — stat one file + check pid; trivial on a Pi.
- All state in **`data/loop_state.json`** (atomic temp+replace, same pattern as
  `progress_tracker._save_progress`), written after **every transition**. Holds: ordered queue,
  and per-museum `{phase, attempts:{research,build,triage}, last_processed_count,
  last_progress_mtime, run_pid, started_at, rate_override, baseline_total, needs_human, notes[]}`.
- **Exit marker**: the driver writes the child's reaped exit code to `data/<slug>/run_exit.json`,
  because a driver restart loses the in-memory exit code. On driver restart: child still alive →
  reattach by pid+cmdline; child gone with no marker → treat as crash → rung 1 (safe, resumable).

---

## 4. TRIAGE AGENT CONTRACT

**Context it receives (bounded):**
- Slug + `docs/<slug>.md` (RESEARCH output — source of truth for endpoints/licence).
- **Log tail:** last ~200 lines / 32 KB cap (enough for traceback + recent progress).
- **Progress delta:** processed_count now vs RUN start, elapsed, last mtime, current rate.
- **Prior triage attempts:** one-line summary each (what changed + outcome), from
  `loop_state.json.notes` → prevents repeating a failed fix.
- The exact failing signal (stall Xh / non-zero exit + code / verifier failure JSON).

**Allowed to modify (enforced by `--disallowedTools` path allow-list + post-run audit):**
- ONLY `src/museums/<slug>.py`, that museum's `config.py` entry (rate/params), its factory in
  `schemas.py`.
- **Forbidden:** `scripts/verify_museum.py`, `base.py`, `progress_tracker.py`, the driver, queue/
  state files, other museums.
- **Audit:** driver runs `git diff --name-only` after the agent exits; any out-of-allow-list file →
  **void attempt + NEEDS_HUMAN**. Driver `git checkout`s the verifier before re-verifying.

**Signaling fixed vs can't-fix:** agent writes `data/<slug>/triage_verdict.json`
`{verdict:"fixed"|"cannot_fix", summary, changed_files[], hypothesis}`.
- `"fixed"` is **not trusted**: driver independently re-runs `verify_museum.py`, then resumes RUN;
  the resume must show progress advancing past the stall point within one poll window, else the
  "fix" is rejected (counts as a failed attempt).
- `"cannot_fix"` → NEEDS_HUMAN with the hypothesis attached.

**Anti-cheat — prevent silent scope-narrowing** (agent making the run "succeed" by shrinking it):
- Driver records **`baseline_total`** at first VALIDATE (from `get_collection_info()` /
  ID-list length; and the "~N images" figure in `docs/<slug>.md`).
- On every resume, driver re-checks the total; if it has **shrunk beyond tolerance** (e.g. 200K → 300)
  → scope-narrowing → void + NEEDS_HUMAN. The driver (not the agent) owns and compares the baseline,
  so it can't be gamed. Param changes also surface in the git-diff audit.

---

## 5. HUMAN ESCALATION (headless Pi — no `osascript`)

Two mechanisms, both **no new pip deps**, driven by one `notify(title, body)` in the driver:
1. **`NEEDS_HUMAN.md`** (repo root) — source of truth, appended per incident, greppable, survives
   reboot. Always written.
2. **A push that reaches your phone** (headless Pi → desktop notifications are useless):
   - **Recommended: ntfy.sh** via `urllib.request` POST (stdlib) to a private topic — free, no
     account, instant phone push. External service; message is low-sensitivity (slug + reason).
   - **Fallback: smtplib email** (stdlib) to your Gmail via an app password — no SaaS beyond SMTP,
     but needs a secret in env.
   - If push fails, the file still exists.

**Message must contain (so you act without re-reading logs):** slug; phase; which rung failed; the
exact failing signal (stall Xh / exit code N / which verifier check failed, with values); last 15
log lines; what triage tried + its hypothesis; the single hand-back command; a one-line "most
likely cause."

**Hand control back:** tiny CLI on top of the same state file —
`python scripts/loop.py resume <slug> [--from research|build|validate|run]` (flips phase, clears
`needs_human`), `python scripts/loop.py skip <slug>`. These mutate `loop_state.json` atomically; the
long-running driver polls the file and picks it up on the next tick.

---

## 6. SCHEDULING — sequential vs. pipelined

- **Strictly sequential** total ≈ Σ RUN durations. RUN dominates (RESEARCH/BUILD/VALIDATE are
  minutes–hours). cma alone ~3–4 days; met ~490K×2 s ≈ **11 days**; loc 1M+ → **weeks**. Several
  net-new museums back-to-back = weeks-to-months.
- **Recommendation: one RUN slot + one AGENT slot.** Keep a single big download running at a time,
  but let the **next** museum's RESEARCH→BUILD→VALIDATE proceed concurrently with the current RUN.
  Since the user's value is *adding* museums (agent work), overlapping it with a multi-day
  unattended RUN is the right trade. **Not** full parallel RUNs.
- **Why not parallel RUNs:** shared single SQLite DB + Pi SD-card I/O = write contention; the
  global `requests_cache` footgun (`base.py:30`); constrained Pi bandwidth/CPU (Playwright).
- **Added failure modes of the pipelined version + mitigations:**
  - B's VALIDATE writing rows while A's RUN writes → `database is locked`. → VALIDATE/verifier
    **samples without persisting** (already specified in §2), so no shared-DB writes.
  - Pi resource contention. → agent slot does no heavy download; VALIDATE sample is 15 items.
  - Two children to track. → `loop_state.json` carries both `run_pid` and `agent_pid`. Acceptable.

---

## 7. FAILURE MODES (ranked by likelihood, cheapest mitigation first)

1. **Rate-limit / ban mid-run (429/403)** — very likely on multi-day runs. → rung-2 baseline backoff
   via env + resume; existing per-request `Retry` (`base.py:46`, `met.py:54`) + met adaptive 403
   backoff handle transients.
2. **Agent invents a nonexistent endpoint / wrong field** — classic LLM failure. → VALIDATE + the
   **independent verifier** catch it (zero rows / bad URL / licence leak) *before* RUN. RESEARCH doc
   must cite the real endpoint the VALIDATE step then exercises live.
3. **Driver dies mid-run (Pi reboot/power)** — likely on a Pi. → **systemd `Restart=on-failure` +
   `WantedBy=multi-user.target`** (auto-start on boot); `loop_state.json` + `run_exit.json` resume;
   RUN child survives or resumes from checkpoint (idempotent).
4. **Disk fills (SD/USB; loc 1M+ images)** — likely at scale. → driver checks `shutil.disk_usage`
   (stdlib) before each RUN and each poll; below threshold → pause + NEEDS_HUMAN. Also set per-museum
   `max_storage_gb` (already supported: `config.py:336`, `artwork_downloader.py:48`).
5. **Agent marks a museum done without downloading** — → only the **driver** sets DONE, and only on
   exit 0 + end-of-collection line + `processed_count > 0` consistent with `baseline_total`
   (scope-narrow guard, §4). Agent can never self-declare DONE.
6. **Agent edits `verify_museum.py` / other forbidden files** — → path allow-list + post-run
   `git diff` audit + driver `git checkout`s the verifier before running it (committed version always
   runs). Out-of-scope change → void + NEEDS_HUMAN.
7. **Silent hang (stuck socket / Playwright)** — → adaptive stall detection → rung 1 restart; plus a
   subprocess wall-clock watchdog.
8. **Metadata schema drift** — factory returns None/raises → verifier non-empty/sample fails → TRIAGE.
9. **Global `requests_cache` collision (`base.py:30`)** — only with in-process parallelism; mitigated
   by subprocess-per-museum + single RUN slot; make per-museum in Phase 0.

---

## Agent Runtime — `claude -p` vs Agent SDK (your evaluation request)

| Axis | `claude -p` headless subprocess (**recommended**) | Python Agent SDK |
|---|---|---|
| **Cost** | Uses whatever the CLI is logged in with. Logged in with a **Claude subscription (OAuth in `~/.claude`) → draws on subscription quota, not metered API**. Can also use an API key if you set one. | Talks to the Anthropic API via `ANTHROPIC_API_KEY` → **metered API billing only**; does not use your subscription. |
| **Complexity** | Low: `subprocess` + `--output-format json`, scope tools with `--allowedTools`/`--disallowedTools`, `--permission-mode`, `--max-turns`, `--model`. No SDK glue. | Higher: manage client, streaming, per-tool permission callbacks in Python. |
| **Flexibility** | Coarser but sufficient (tool-scoping via flags). | Finest-grained (hooks, programmatic control) — more than this loop needs. |
| **Fit** | Driver is already a subprocess orchestrator (RUN shells out to `main.py`); agents-as-subprocess keep one uniform model. "RUN = zero agent tokens" falls out (RUN never spawns `claude`). | Would blur the RUN boundary and add API cost. |

**Recommendation: `claude -p`.** Caveats to verify on the Pi: (1) Node 18+ and Claude Code run on
**ARM64 Linux**; (2) confirm non-interactive `-p` respects the **subscription OAuth** in `~/.claude`
under systemd's environment; (3) **subscription usage caps** (5-hour windows) could throttle a burst
of agent spawns — mitigated because RESEARCH/BUILD/TRIAGE are infrequent (per museum) and RUN uses none.

---

## Driver design (`scripts/loop.py`, stdlib only)

- State machine over `data/loop_state.json`; write after every transition; SIGTERM (systemd stop)
  persists state and leaves children resumable.
- `run_agent(phase, slug)` → `claude -p` with per-phase tool scoping:
  - RESEARCH: **read-only** tools + web; writes `docs/<slug>.md`; may not touch `src/`.
  - BUILD: may edit `src/museums/<slug>.py`, `schemas.py`, register in `config.py`/`main.py`.
  - TRIAGE: allow-list of §4.
- `run_download(slug, rate_override)` → `python main.py -m <slug>`; poll `processed_ids.json`.
- `verify(slug)` → `git checkout scripts/verify_museum.py` then run it in a fresh subprocess.
- `poll_run`, `notify`, `escalate`, plus the `resume`/`skip` CLI.

**New files (justified):** `scripts/loop.py` (driver — nothing existing fits),
`scripts/verify_museum.py` (must be independent of museum code), `docs/<slug>.md` (research outputs),
`NEEDS_HUMAN.md` (escalation), `scripts/artserve-loop.service` (systemd unit; tmux is the
low-ceremony alternative). Everything else = edits to existing `base.py`, `schemas.py`,
`progress_tracker.py`, `config.py`, `main.py`, and the 3 reference museums (Phase 0).

---

## Verification (how to test without a multi-week run)

1. **Verifier unit checks:** run against `aic` → PASS; against a clone with a deliberately broken
   image URL → FAIL (check D); against a config that leaks non-public-domain → FAIL (check B);
   simulate a source outage → INCONCLUSIVE (exit 2), not FAIL.
2. **State-machine dry-run** with synthetic museums: a 60 s "RUN" that writes `processed_ids`
   incrementally (happy path), one that hangs (→ stall → rung 1), one that exits non-zero (→ crash →
   rung 1), one that narrows scope (→ NEEDS_HUMAN via baseline guard).
3. **Real end-to-end** with an existing museum: `main.py -m <slug> --limit 20` through
   VALIDATE→RUN→DONE — proves poll/summary/exit-code/DONE path.
4. **Crash resilience:** `kill -9` the driver mid-run, restart → resumes without losing place
   (`loop_state.json` + `run_exit.json`).
5. **Escalation:** force NEEDS_HUMAN → confirm ntfy push + `NEEDS_HUMAN.md` + `loop.py resume` handback.
6. **Pi smoke test:** confirm `claude -p` uses subscription auth headless under systemd, and Playwright/
   Chromium availability on ARM64 (only if a net-new museum needs scraping — prefer non-scraping sources).

---

## Execution order (for the follow-up implementation task — not this task)

1. **Phase 0** seam refactor + enabler fixes (`base.py`, `schemas.py`, `progress_tracker.py`,
   `config.py`, `main.py`, aic/met/cma) — with the existing test suite green.
2. `scripts/verify_museum.py` + its unit checks.
3. `scripts/loop.py` state machine + `loop_state.json` persistence + poll/liveness.
4. Agent-invocation wrappers (`claude -p` scoping per phase) + triage contract + audits.
5. Escalation (`NEEDS_HUMAN.md` + ntfy) + `resume`/`skip` CLI.
6. systemd unit + Pi smoke test.
