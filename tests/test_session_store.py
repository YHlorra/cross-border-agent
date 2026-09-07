""" addendum — per-session chat store helpers (Python mirror).

The TypeScript helpers in ``lib/session-store.ts`` are pure functions over a
flat ``Record<sessionId, SessionEntry[]>``. This mirror exercises the same
algorithms in Python so we can SEAM-test concurrency invariants without
spinning up Next dev + playwright + a real LLM stream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class RunState:
    phase: Literal["running", "final", "error", "cancelled", "empty"] = "running"
    candidates: list = field(default_factory=list)


SessionEntry = dict  # {kind, ...} — dict is close enough for pure-fold tests


def session_busy(entries: list[SessionEntry] | None) -> bool:
    if not entries:
        return False
    for e in reversed(entries):
        if e.get("kind") in ("agent", "listing"):
            return e["run"]["phase"] == "running"
    return False


def running_session_ids(entries_by_session: dict[str, list[SessionEntry]]) -> set[str]:
    return {sid for sid, entries in entries_by_session.items() if session_busy(entries)}


def append_entry(entries: list[SessionEntry], entry: SessionEntry) -> list[SessionEntry]:
    return [*entries, entry]


def patch_entry_run(
    entries: list[SessionEntry], entry_id: int, patch
) -> list[SessionEntry]:
    found = False
    out = []
    for e in entries:
        if e.get("id") != entry_id:
            out.append(e)
            continue
        found = True
        out.append(patch(e))
    return out if found else entries


def mark_entry_cancelled(entries: list[SessionEntry], entry_id: int) -> list[SessionEntry]:
    def _patch(e: SessionEntry) -> SessionEntry:
        if e.get("kind") not in ("agent", "listing"):
            return e
        return {**e, "run": {**e["run"], "phase": "cancelled"}}

    return patch_entry_run(entries, entry_id, _patch)


# ─── Tests ─────────────────────────────────────────────────────────────────


def test_session_busy_detects_running_agent():
    entries = [
        {"kind": "user", "text": "q"},
        {"id": 1, "kind": "agent", "run": {"phase": "running"}},
    ]
    assert session_busy(entries) is True


def test_session_busy_detects_running_listing():
    entries = [{"id": 1, "kind": "listing", "run": {"phase": "running"}}]
    assert session_busy(entries) is True


def test_session_busy_ignores_terminal_runs():
    for phase in ("final", "error", "cancelled", "empty"):
        entries = [{"id": 1, "kind": "agent", "run": {"phase": phase}}]
        assert session_busy(entries) is False, f"phase={phase} should not be busy"


def test_session_busy_scans_from_tail_only():
    """Old runs are ignored once a later non-running entry appears."""
    entries = [
        {"id": 1, "kind": "agent", "run": {"phase": "running"}},
        {"id": 2, "kind": "agent", "run": {"phase": "final"}},
    ]
    assert session_busy(entries) is False


def test_session_busy_handles_empty_and_undefined():
    assert session_busy(None) is False
    assert session_busy([]) is False


def test_running_session_ids_collects_independent_sessions():
    """A-side running + B-side idle + C-side running → set has both."""
    esm = {
        "A": [{"id": 1, "kind": "agent", "run": {"phase": "running"}}],
        "B": [{"id": 1, "kind": "agent", "run": {"phase": "final"}}],
        "C": [{"id": 1, "kind": "listing", "run": {"phase": "running"}}],
    }
    assert running_session_ids(esm) == {"A", "C"}


def test_append_entry_returns_new_list():
    a = [{"kind": "user", "text": "q"}]
    b = append_entry(a, {"id": 1, "kind": "agent", "run": {"phase": "running"}})
    assert a == [{"kind": "user", "text": "q"}]  # immutable
    assert len(b) == 2


def test_patch_entry_run_replaces_matching_id():
    entries = [
        {"id": 1, "kind": "agent", "run": {"phase": "running"}},
        {"id": 2, "kind": "listing", "run": {"phase": "running"}},
    ]
    out = patch_entry_run(
        entries, 1, lambda e: {**e, "run": {**e["run"], "phase": "final"}}
    )
    assert out[0]["run"]["phase"] == "final"
    assert out[1]["run"]["phase"] == "running"  # untouched


def test_patch_entry_run_no_op_when_id_missing():
    entries = [{"id": 1, "kind": "agent", "run": {"phase": "running"}}]
    out = patch_entry_run(entries, 999, lambda e: e)
    assert out is entries  # same reference, no churn


def test_mark_entry_cancelled_sets_phase():
    entries = [{"id": 1, "kind": "agent", "run": {"phase": "running"}}]
    out = mark_entry_cancelled(entries, 1)
    assert out[0]["run"]["phase"] == "cancelled"


def test_concurrent_session_isolation():
    """Per-session state must not bleed across — the whole point of the
    addendum. Patch A's run must not touch B's entries."""
    esm = {
        "A": [{"id": 1, "kind": "agent", "run": {"phase": "running"}}],
        "B": [{"id": 1, "kind": "agent", "run": {"phase": "running"}}],
    }
    # Finalize only A's run
    esm["A"] = patch_entry_run(
        esm["A"], 1, lambda e: {**e, "run": {**e["run"], "phase": "final"}}
    )
    assert esm["A"][0]["run"]["phase"] == "final"
    assert esm["B"][0]["run"]["phase"] == "running"
    assert running_session_ids(esm) == {"B"}