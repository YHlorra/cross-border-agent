"""R10 messages 事件行 store — SEAM tests .

Pure DB SEAM — no LLM, no network. Covers:
- new schema: event_type / run_id / sequence / payload / answer_failed columns
- UNIQUE(session_id, run_id, sequence) enforcement
- save_event / list_session_events / list_session_runs / mark_run_failed
- legacy schema migration (event_type column absence → drop; rows present → rename)
- legacy wrappers save_message / list_messages still functional

Each test uses an isolated tmp_path DB to avoid touching the real memory.db.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent.persistence import (
    init_db,
    list_messages,
    list_session_events,
    list_session_runs,
    mark_run_failed,
    save_event,
    save_message,
)


@pytest.fixture
def db() -> None:
    """PG-only：db_path 哨兵 None——隔离由 conftest 的
    _clean_app_tables autouse fixture（每测试 TRUNCATE 应用表）提供，
    PG 后端忽略 db_path。"""
    return None


# ─── save_event round-trip ─────────────────────────────────────────────────


def test_save_event_minimum_returns_id(db: None):
    mid = save_event(
        session_id="s1",
        run_id="r1",
        event_type="node_start",
        sequence=0,
        payload={"event": "node_start", "node": "intent"},
        db_path=db,
    )
    assert isinstance(mid, str) and len(mid) > 0


def test_save_event_persists_all_fields(db: None):
    save_event(
        session_id="s1",
        run_id="r1",
        event_type="final",
        sequence=3,
        payload={"event": "final", "decision": "go", "candidates": []},
        answer_failed=False,
        db_path=db,
    )
    rows = list_session_events(session_id="s1", db_path=db)
    assert len(rows) == 1
    r = rows[0]
    assert r["session_id"] == "s1"
    assert r["run_id"] == "r1"
    assert r["event_type"] == "final"
    assert r["sequence"] == 3
    assert r["payload"]["decision"] == "go"
    assert r["answer_failed"] is False


def test_save_event_answer_failed_true(db: None):
    save_event(
        session_id="s1",
        run_id="r-fail",
        event_type="error",
        sequence=0,
        payload={"event": "error", "code": "BadRequest", "message": "x"},
        answer_failed=True,
        db_path=db,
    )
    rows = list_session_events(session_id="s1", db_path=db)
    assert rows[0]["answer_failed"] is True


def test_save_event_unique_constraint(db: None):
    """durable-runs 契约修订 — 重复 (session_id, run_id, sequence) 幂等:
    不抛错、不产生重复行（durable workflow 崩溃续跑依赖此语义）。"""
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
        event_type="tool_call",
        sequence=0,
        payload={"event": "tool_call"},
        db_path=db,
    )
    rows = list_session_events(session_id="s1", db_path=db)
    assert len(rows) == 1


def test_save_event_different_runs_same_sequence_allowed(db: None):
    """UNIQUE is (session_id, run_id, sequence) — same seq in different runs OK."""
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
        run_id="r2",
        event_type="node_start",
        sequence=0,
        payload={"event": "node_start"},
        db_path=db,
    )
    rows = list_session_events(session_id="s1", db_path=db)
    assert len(rows) == 2


# ─── list_session_events filters ───────────────────────────────────────────


def test_list_session_events_ordered_by_sequence(db: None):
    for seq in [2, 0, 1]:
        save_event(
            session_id="s1",
            run_id="r1",
            event_type="node_start",
            sequence=seq,
            payload={"event": "node_start", "seq": seq},
            db_path=db,
        )
    rows = list_session_events(session_id="s1", db_path=db)
    assert [r["sequence"] for r in rows] == [0, 1, 2]


def test_list_session_events_orders_runs_by_first_event_time(db: None):
    for run_id in ("z-later", "a-earlier"):
        for sequence in (0, 1):
            save_event(
                session_id="s1",
                run_id=run_id,
                event_type="node_start",
                sequence=sequence,
                payload={"event": "node_start", "run_id": run_id, "sequence": sequence},
                db_path=db,
            )

    from tests.conftest import TEST_DB_URL
    import psycopg

    with psycopg.connect(TEST_DB_URL) as conn:
        conn.execute(
            "UPDATE messages SET created_at = %s WHERE run_id = %s AND sequence = 0",
            ("2026-09-01T00:00:02+00:00", "z-later"),
        )
        conn.execute(
            "UPDATE messages SET created_at = %s WHERE run_id = %s AND sequence = 1",
            ("2026-09-01T00:00:03+00:00", "z-later"),
        )
        conn.execute(
            "UPDATE messages SET created_at = %s WHERE run_id = %s AND sequence = 0",
            ("2026-09-01T00:00:01+00:00", "a-earlier"),
        )
        conn.execute(
            "UPDATE messages SET created_at = %s WHERE run_id = %s AND sequence = 1",
            ("2026-09-01T00:00:04+00:00", "a-earlier"),
        )
        conn.commit()

    rows = list_session_events(session_id="s1", db_path=db)
    assert [(r["run_id"], r["sequence"]) for r in rows] == [
        ("a-earlier", 0),
        ("a-earlier", 1),
        ("z-later", 0),
        ("z-later", 1),
    ]


def test_list_session_events_keeps_sequence_order_when_run_timestamps_tie(db: None):
    for sequence in (2, 0, 1):
        save_event(
            session_id="s1",
            run_id="r1",
            event_type="node_start",
            sequence=sequence,
            payload={"event": "node_start", "sequence": sequence},
            db_path=db,
        )

    from tests.conftest import TEST_DB_URL
    import psycopg

    with psycopg.connect(TEST_DB_URL) as conn:
        conn.execute(
            "UPDATE messages SET created_at = %s WHERE run_id = %s",
            ("2026-09-01T00:00:01+00:00", "r1"),
        )
        conn.commit()

    rows = list_session_events(session_id="s1", db_path=db)
    assert [r["sequence"] for r in rows] == [0, 1, 2]


def test_list_session_events_filter_by_type(db: None):
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
        event_type="tool_call",
        sequence=1,
        payload={"event": "tool_call"},
        db_path=db,
    )
    save_event(
        session_id="s1",
        run_id="r1",
        event_type="node_end",
        sequence=2,
        payload={"event": "node_end"},
        db_path=db,
    )
    rows = list_session_events(
        session_id="s1", event_type="node_start", db_path=db
    )
    assert len(rows) == 1
    assert rows[0]["event_type"] == "node_start"

    rows_multi = list_session_events(
        session_id="s1", event_type=["node_start", "node_end"], db_path=db
    )
    assert {r["event_type"] for r in rows_multi} == {"node_start", "node_end"}


def test_list_session_events_exclude_failed(db: None):
    save_event(
        session_id="s1",
        run_id="r-ok",
        event_type="node_start",
        sequence=0,
        payload={"event": "node_start"},
        answer_failed=False,
        db_path=db,
    )
    save_event(
        session_id="s1",
        run_id="r-fail",
        event_type="error",
        sequence=0,
        payload={"event": "error", "code": "x"},
        answer_failed=True,
        db_path=db,
    )
    # default include_failed=True returns both
    all_rows = list_session_events(session_id="s1", db_path=db)
    assert len(all_rows) == 2
    # include_failed=False filters out the failed run's events
    ok_only = list_session_events(
        session_id="s1", include_failed=False, db_path=db
    )
    assert len(ok_only) == 1
    assert ok_only[0]["run_id"] == "r-ok"


# ─── list_session_runs aggregation ─────────────────────────────────────────


def test_list_session_runs_groups_by_run_id(db: None):
    for run_id, seqs in [("r1", [0, 1, 2]), ("r2", [0, 1])]:
        for seq in seqs:
            save_event(
                session_id="s1",
                run_id=run_id,
                event_type="node_start" if seq == 0 else "node_end",
                sequence=seq,
                payload={"event": "node_start" if seq == 0 else "node_end"},
                db_path=db,
            )
    runs = list_session_runs(session_id="s1", db_path=db)
    by_id = {r["run_id"]: r for r in runs}
    assert by_id["r1"]["event_count"] == 3
    assert by_id["r2"]["event_count"] == 2
    # first_event_at < last_event_at
    assert by_id["r1"]["first_event_at"] <= by_id["r1"]["last_event_at"]


def test_list_session_runs_extracts_decision_from_final(db: None):
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
        payload={"event": "final", "decision": "caution"},
        db_path=db,
    )
    runs = list_session_runs(session_id="s1", db_path=db)
    assert runs[0]["decision"] == "caution"


def test_list_session_runs_no_final_event(db: None):
    save_event(
        session_id="s1",
        run_id="r1",
        event_type="error",
        sequence=0,
        payload={"event": "error", "code": "X"},
        answer_failed=True,
        db_path=db,
    )
    runs = list_session_runs(session_id="s1", db_path=db)
    assert runs[0]["decision"] == ""  # no final → empty string


# ─── mark_run_failed ───────────────────────────────────────────────────────


def test_mark_run_failed_sets_all_events(db: None):
    for seq in range(3):
        save_event(
            session_id="s1",
            run_id="r-fail",
            event_type="node_start" if seq == 0 else "node_end",
            sequence=seq,
            payload={"event": "x"},
            db_path=db,
        )
    save_event(
        session_id="s1",
        run_id="r-ok",
        event_type="node_start",
        sequence=0,
        payload={"event": "x"},
        db_path=db,
    )
    n = mark_run_failed(run_id="r-fail", db_path=db)
    assert n == 3
    # r-fail events now filtered out
    visible = list_session_events(
        session_id="s1", include_failed=False, db_path=db
    )
    assert all(r["run_id"] == "r-ok" for r in visible)


def test_mark_run_failed_unknown_run_zero_rows(db: None):
    save_event(
        session_id="s1",
        run_id="r1",
        event_type="node_start",
        sequence=0,
        payload={"event": "x"},
        db_path=db,
    )
    n = mark_run_failed(run_id="does-not-exist", db_path=db)
    assert n == 0


# ─── legacy schema migration ───────────────────────────────────────────────


def test_legacy_schema_migration_retired() -> None:
    """SQLite 时代的 legacy messages 表迁移（drop/rename）已随双驱动退役：
    PG-only 下 schema 由 db/（Drizzle）独占，init_db 只做存在性校验。"""
    assert True  # 行为档案保留在此注释里,不再有可测的迁移路径



# ─── legacy save_message / list_messages wrappers ──────────────────────────


def test_legacy_save_message_routes_to_save_event(db: None):
    save_message(
        session_id="s1", role="user", content="hello", db_path=db
    )
    rows = list_session_events(
        session_id="s1", event_type="message", db_path=db
    )
    assert len(rows) == 1
    assert rows[0]["payload"]["role"] == "user"
    assert rows[0]["payload"]["content"] == "hello"


def test_legacy_list_messages_returns_role_content(db: None):
    save_message(session_id="s1", role="user", content="hi", db_path=db)
    save_message(
        session_id="s1", role="assistant", content="hello back", db_path=db
    )
    msgs = list_messages(session_id="s1", db_path=db)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hi"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "hello back"
    # wrapper preserves id + created_at from the typed row
    for m in msgs:
        assert "id" in m and "created_at" in m

# ───  listing turn persistence (server throat contract) ───────


def test_listing_turn_events_roundtrip(db: None):
    """The listing_run handler persists user_message(kind=listing_turn) at
    sequence=0 then every wire event — same shape as a selection run, so
    session replay can rebuild the turn."""
    save_event(
        session_id="s-l",
        run_id="r-l",
        event_type="user_message",
        sequence=0,
        payload={
            "kind": "listing_turn",
            "query": "为「Cat Tree」生成 Listing",
            "candidate": {"product_id": "p1", "name_en": "Cat Tree"},
            "market_code": "US",
            "brand": "",
        },
        db_path=db,
    )
    save_event(
        session_id="s-l",
        run_id="r-l",
        event_type="listing_chunk",
        sequence=1,
        payload={"event": "listing_chunk", "delta": "Cat"},
        db_path=db,
    )
    save_event(
        session_id="s-l",
        run_id="r-l",
        event_type="final",
        sequence=2,
        payload={"event": "final", "kind": "listing", "run_id": "r-l"},
        db_path=db,
    )

    rows = list_session_events(session_id="s-l", db_path=db)
    assert [r["event_type"] for r in rows] == [
        "user_message",
        "listing_chunk",
        "final",
    ]
    assert rows[0]["payload"]["kind"] == "listing_turn"
    assert rows[0]["run_id"] == "r-l"
    assert rows[2]["payload"]["run_id"] == "r-l"


def test_listing_failed_error_row_excluded_by_default(db: None):
    """Error rows persisted with answer_failed=1 are invisible to the
    include_failed=False read (the server events endpoint's default /
    context injection filter); the replay contract relies on this and
    demotes the turn instead."""
    save_event(
        session_id="s-l",
        run_id="r-l",
        event_type="error",
        sequence=1,
        payload={"event": "error", "code": "X", "message": "boom"},
        answer_failed=True,
        db_path=db,
    )
    default_rows = list_session_events(
        session_id="s-l", include_failed=False, db_path=db
    )
    audit_rows = list_session_events(
        session_id="s-l", include_failed=True, db_path=db
    )
    assert default_rows == []
    assert len(audit_rows) == 1
