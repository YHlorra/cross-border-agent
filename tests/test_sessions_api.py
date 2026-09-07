"""R10 sessions store + handlers — SEAM tests .

Pure DB SEAM. Covers:
- list_sessions aggregate
- delete_session_events row count + cascade
- /run events land in messages (covered via p1/p9 E2E; here unit-only)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.persistence import (
    delete_session_events,
    list_sessions,
    save_event,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """Fresh isolated DB for each test."""
    return tmp_path / "messages.db"


def test_list_sessions_empty(db: Path):
    assert list_sessions(db_path=db) == []


def test_list_sessions_aggregates_one_session(db: Path):
    save_event(
        session_id="s1",
        run_id="r1",
        event_type="node_start",
        sequence=0,
        payload={"event": "node_start"},
        db_path=db,
    )
    save_event(
        session_id="s1",
        run_id="r1",
        event_type="final",
        sequence=1,
        payload={"event": "final", "decision": "go"},
        db_path=db,
    )
    sessions = list_sessions(db_path=db)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["session_id"] == "s1"
    assert s["event_count"] == 2
    assert s["run_count"] == 1
    assert s["has_failed"] is False


def test_list_sessions_multiple_runs_aggregates_runs(db: Path):
    for run in ("r1", "r2", "r3"):
        save_event(
            session_id="s1",
            run_id=run,
            event_type="node_start",
            sequence=0,
            payload={"event": "node_start"},
            db_path=db,
        )
        save_event(
            session_id="s1",
            run_id=run,
            event_type="final",
            sequence=1,
            payload={"event": "final", "decision": "caution"},
            db_path=db,
        )
    s = list_sessions(db_path=db)
    assert len(s) == 1
    assert s[0]["run_count"] == 3
    assert s[0]["event_count"] == 6


def test_list_sessions_has_failed_propagates(db: Path):
    save_event(
        session_id="s1",
        run_id="r1",
        event_type="error",
        sequence=0,
        payload={"event": "error", "code": "X"},
        answer_failed=True,
        db_path=db,
    )
    save_event(
        session_id="s1",
        run_id="r2",
        event_type="final",
        sequence=0,
        payload={"event": "final", "decision": "go"},
        db_path=db,
    )
    s = list_sessions(db_path=db)
    assert s[0]["has_failed"] is True


def test_delete_session_events_returns_count(db: Path):
    for seq in range(3):
        save_event(
            session_id="s1",
            run_id="r1",
            event_type="node_start",
            sequence=seq,
            payload={"event": "x"},
            db_path=db,
        )
    save_event(
        session_id="s2",
        run_id="r1",
        event_type="node_start",
        sequence=0,
        payload={"event": "x"},
        db_path=db,
    )
    n = delete_session_events(session_id="s1", db_path=db)
    assert n == 3
    # s2 untouched
    s2 = list_sessions(db_path=db)
    assert {s["session_id"] for s in s2} == {"s2"}
    # s1 fully gone
    assert list_sessions(db_path=db) == [s for s in s2 if s["session_id"] == "s2"]


def test_delete_session_events_empty_session_zero(db: Path):
    save_event(
        session_id="s1",
        run_id="r1",
        event_type="node_start",
        sequence=0,
        payload={"event": "x"},
        db_path=db,
    )
    n = delete_session_events(session_id="does-not-exist", db_path=db)
    assert n == 0
    # s1 preserved
    assert list_sessions(db_path=db)[0]["session_id"] == "s1"