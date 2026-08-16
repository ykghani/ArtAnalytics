"""
Unit tests for the pure/testable helpers in scripts/loop.py.

These exercise the logic added/changed by the loop.py review: the
identical-diff false-positive classifier, retry-feedback prompt snippets,
the non-blocking RUN scheduler's museum-selection logic, and the
per-museum rate-limit env var / credential-gate helpers. No real git
subprocesses, agent subprocesses, or network calls — git and Settings
access are mocked.
"""

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import loop


# ---------------------------------------------------------------------------
# _classify_forbidden_files — the identical-diff false-positive detector
# ---------------------------------------------------------------------------

def _fake_run_cmd_capture(diffs_by_cwd):
    """Build a _run_cmd_capture stand-in: `diffs_by_cwd` maps
    (cwd_label, rel_path) -> diff text. `cwd_label` is "wt" for any cwd that
    isn't PROJECT_ROOT, and "root" for PROJECT_ROOT itself."""
    def fake(cmd, cwd=loop.PROJECT_ROOT):
        rel = cmd[-1]
        label = "root" if cwd == loop.PROJECT_ROOT else "wt"
        diff = diffs_by_cwd.get((label, rel), "")
        return (0, diff, "")
    return fake


def test_classify_forbidden_files_identical_diff_is_infra():
    """A forbidden file whose worktree diff matches PROJECT_ROOT's own
    uncommitted diff byte-for-byte is the stale/dirty-HEAD signature —
    classified as infra, not a genuine agent edit."""
    diff_text = "diff --git a/scripts/loop.py b/scripts/loop.py\n+something\n"
    fake = _fake_run_cmd_capture({
        ("wt", "scripts/loop.py"): diff_text,
        ("root", "scripts/loop.py"): diff_text,
    })
    with patch.object(loop, "_run_cmd_capture", side_effect=fake):
        infra, genuine = loop._classify_forbidden_files(
            "testslug", Path("/fake/worktree"), ["scripts/loop.py"]
        )
    assert [rel for rel, _ in infra] == ["scripts/loop.py"]
    assert genuine == []


def test_classify_forbidden_files_different_diff_is_genuine():
    """A worktree diff that differs from PROJECT_ROOT's own diff (or where
    PROJECT_ROOT has no pending diff at all) is a real agent edit."""
    fake = _fake_run_cmd_capture({
        ("wt", "src/museums/other.py"): "diff --git a/src/museums/other.py ...\n+agent wrote this\n",
        ("root", "src/museums/other.py"): "",
    })
    with patch.object(loop, "_run_cmd_capture", side_effect=fake):
        infra, genuine = loop._classify_forbidden_files(
            "testslug", Path("/fake/worktree"), ["src/museums/other.py"]
        )
    assert infra == []
    assert [rel for rel, _ in genuine] == ["src/museums/other.py"]


def test_classify_forbidden_files_mixed():
    """A batch with both kinds is split correctly — one infra, one genuine."""
    identical = "same diff\n"
    fake = _fake_run_cmd_capture({
        ("wt", "scripts/loop.py"): identical,
        ("root", "scripts/loop.py"): identical,
        ("wt", "src/database/migrate_to_postgres.py"): "diff --git ...\n+real edit\n",
        ("root", "src/database/migrate_to_postgres.py"): "",
    })
    with patch.object(loop, "_run_cmd_capture", side_effect=fake):
        infra, genuine = loop._classify_forbidden_files(
            "testslug", Path("/fake/worktree"),
            ["scripts/loop.py", "src/database/migrate_to_postgres.py"],
        )
    assert [rel for rel, _ in infra] == ["scripts/loop.py"]
    assert [rel for rel, _ in genuine] == ["src/database/migrate_to_postgres.py"]


def test_classify_forbidden_files_diagnostic_failure_is_genuine():
    """If the diff comparison itself raises, never silently swallow a real
    escalation — treat the file as genuine."""
    def raising(cmd, cwd=loop.PROJECT_ROOT):
        raise RuntimeError("git blew up")

    with patch.object(loop, "_run_cmd_capture", side_effect=raising):
        infra, genuine = loop._classify_forbidden_files(
            "testslug", Path("/fake/worktree"), ["some/file.py"]
        )
    assert infra == []
    assert [rel for rel, _ in genuine] == ["some/file.py"]


def test_handle_forbidden_files_all_infra_does_not_escalate():
    """When every forbidden file is an infra false positive, the audit should
    pass through as if it never failed — no escalate() call, and the infra
    files are dropped from `changed` so they're never synced back."""
    identical = "same diff\n"
    fake = _fake_run_cmd_capture({
        ("wt", "scripts/loop.py"): identical,
        ("root", "scripts/loop.py"): identical,
    })
    ms = loop._default_museum_state("testslug")
    with patch.object(loop, "_run_cmd_capture", side_effect=fake), \
         patch.object(loop, "escalate") as mock_escalate:
        changed, ok = loop._handle_forbidden_files(
            "testslug", ms, "BUILD", Path("/fake/worktree"), None,
            changed=["src/museums/testslug.py", "scripts/loop.py"],
            forbidden=["scripts/loop.py"],
        )
    assert ok is True
    assert changed == ["src/museums/testslug.py"]
    mock_escalate.assert_not_called()


def test_handle_forbidden_files_genuine_violation_escalates():
    fake = _fake_run_cmd_capture({
        ("wt", "src/utils.py"): "diff --git ...\n+agent edit\n",
        ("root", "src/utils.py"): "",
    })
    ms = loop._default_museum_state("testslug")
    with patch.object(loop, "_run_cmd_capture", side_effect=fake), \
         patch.object(loop, "escalate") as mock_escalate:
        changed, ok = loop._handle_forbidden_files(
            "testslug", ms, "BUILD", Path("/fake/worktree"), None,
            changed=["src/utils.py"],
            forbidden=["src/utils.py"],
        )
    assert ok is False
    mock_escalate.assert_called_once()
    assert "src/utils.py" in mock_escalate.call_args[0][1]


# ---------------------------------------------------------------------------
# _prior_failure_note
# ---------------------------------------------------------------------------

def test_prior_failure_note_empty_when_no_failure():
    ms = loop._default_museum_state("testslug")
    assert loop._prior_failure_note(ms, "RESEARCH") == ""


def test_prior_failure_note_empty_when_different_phase():
    ms = loop._default_museum_state("testslug")
    ms["last_failure"] = {"phase": "BUILD", "attempt": 1, "tail": "boom"}
    assert loop._prior_failure_note(ms, "RESEARCH") == ""


def test_prior_failure_note_included_for_matching_phase():
    ms = loop._default_museum_state("testslug")
    ms["last_failure"] = {"phase": "RESEARCH", "attempt": 2, "tail": "some earlier failure output"}
    note = loop._prior_failure_note(ms, "RESEARCH")
    assert "RESEARCH" in note
    assert "some earlier failure output" in note
    assert "#2" in note


# ---------------------------------------------------------------------------
# _pick_active — RUN exclusion, next_eligible_at backoff, needs_human skip
# ---------------------------------------------------------------------------

def _state_with(museums: dict) -> dict:
    return {"queue": list(museums.keys()), "museums": museums}


def test_pick_active_skips_run_and_terminal_phases():
    state = _state_with({
        "a": {**loop._default_museum_state("a"), "phase": "DONE"},
        "b": {**loop._default_museum_state("b"), "phase": "RUN"},
        "c": {**loop._default_museum_state("c"), "phase": "BUILD"},
    })
    assert loop._pick_active(state) == "c"


def test_pick_active_skips_needs_human():
    state = _state_with({
        "a": {**loop._default_museum_state("a"), "phase": "BUILD", "needs_human": True},
        "b": {**loop._default_museum_state("b"), "phase": "VALIDATE"},
    })
    assert loop._pick_active(state) == "b"


def test_pick_active_skips_future_next_eligible_at():
    state = _state_with({
        "a": {**loop._default_museum_state("a"), "phase": "VALIDATE", "next_eligible_at": time.time() + 1000},
        "b": {**loop._default_museum_state("b"), "phase": "BUILD"},
    })
    assert loop._pick_active(state) == "b"


def test_pick_active_allows_past_next_eligible_at():
    state = _state_with({
        "a": {**loop._default_museum_state("a"), "phase": "VALIDATE", "next_eligible_at": time.time() - 1000},
    })
    assert loop._pick_active(state) == "a"


def test_pick_active_none_when_nothing_active():
    state = _state_with({
        "a": {**loop._default_museum_state("a"), "phase": "DONE"},
        "b": {**loop._default_museum_state("b"), "phase": "RUN"},
    })
    assert loop._pick_active(state) is None


# ---------------------------------------------------------------------------
# _count_active_runs
# ---------------------------------------------------------------------------

def test_count_active_runs():
    state = _state_with({
        "a": {**loop._default_museum_state("a"), "run_pid": 123},
        "b": {**loop._default_museum_state("b"), "run_pid": None},
        "c": {**loop._default_museum_state("c"), "run_pid": 456},
    })
    assert loop._count_active_runs(state) == 2


# ---------------------------------------------------------------------------
# _museum_rate_limit_env_var
# ---------------------------------------------------------------------------

def test_museum_rate_limit_env_var_matches_settings_field_convention():
    # Verified empirically against src/config.py: pydantic-settings 2.x reads
    # SCREAMING_SNAKE_CASE of the field name, not the `env=` kwarg — e.g. CMA's
    # field is cma_rate_limit, so CMA_RATE_LIMIT is what actually works even
    # though its Field(..., env="CLEVELAND_RATE_LIMIT") claims otherwise.
    assert loop._museum_rate_limit_env_var("cma") == "CMA_RATE_LIMIT"
    assert loop._museum_rate_limit_env_var("kunstmuseumbasel") == "KUNSTMUSEUMBASEL_RATE_LIMIT"


# ---------------------------------------------------------------------------
# _check_credential_gate
# ---------------------------------------------------------------------------

def test_credential_gate_proceeds_when_no_summary_file(tmp_path):
    ms = loop._default_museum_state("nosummary")
    with patch.object(loop, "_research_summary_file", return_value=tmp_path / "missing.json"):
        assert loop._check_credential_gate("nosummary", ms) is True


def test_credential_gate_proceeds_when_auth_not_required(tmp_path):
    summary = tmp_path / "research_summary.json"
    summary.write_text(json.dumps({"auth_required": False}))
    ms = loop._default_museum_state("noauth")
    with patch.object(loop, "_research_summary_file", return_value=summary):
        assert loop._check_credential_gate("noauth", ms) is True


def test_credential_gate_proceeds_when_env_var_set(tmp_path, monkeypatch):
    summary = tmp_path / "research_summary.json"
    summary.write_text(json.dumps({
        "auth_required": True, "suggested_env_var": "SOMEMUSEUM_API_KEY",
        "auth_notes": "needs a key", "signup_url": "https://example.com/signup",
    }))
    monkeypatch.setenv("SOMEMUSEUM_API_KEY", "sk-test-123")
    ms = loop._default_museum_state("somemuseum")
    with patch.object(loop, "_research_summary_file", return_value=summary):
        assert loop._check_credential_gate("somemuseum", ms) is True


def test_credential_gate_escalates_when_credential_missing(tmp_path, monkeypatch):
    summary = tmp_path / "research_summary.json"
    summary.write_text(json.dumps({
        "auth_required": True, "suggested_env_var": "HARVARD_API_KEY",
        "auth_notes": "Requires a human to register.",
        "signup_url": "https://harvardartmuseums.org/collections/api",
    }))
    monkeypatch.delenv("HARVARD_API_KEY", raising=False)
    ms = loop._default_museum_state("harvard")
    with patch.object(loop, "_research_summary_file", return_value=summary), \
         patch.object(loop, "escalate") as mock_escalate:
        result = loop._check_credential_gate("harvard", ms)
    assert result is False
    mock_escalate.assert_called_once()
    args, kwargs = mock_escalate.call_args
    assert kwargs.get("category") == "can-wait"


def test_credential_gate_proceeds_on_unparseable_json(tmp_path):
    summary = tmp_path / "research_summary.json"
    summary.write_text("not valid json{{{")
    ms = loop._default_museum_state("badjson")
    with patch.object(loop, "_research_summary_file", return_value=summary):
        assert loop._check_credential_gate("badjson", ms) is True
