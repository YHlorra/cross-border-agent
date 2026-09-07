""" addendum — session_meta SEAM (sidebar context-menu persistence).

Pure SQLite tests over init_db + list_sessions JOIN + update_session_meta +
cascading delete from session_meta when messages are wiped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.persistence import (
    delete_session_events,
    get_session_meta,
    init_db,
    list_sessions,
    save_event,
    update_session_meta,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "session-meta.db"


def _seed_session(db: Path, session_id: str = "s1", events: int = 3) -> None:
    for i in range(events):
        save_event(
            session_id=session_id,
            run_id=f"r{i}",
            event_type="node_start",
            sequence=i + 1,
            payload={"event": "node_start", "node": "intent"},
            db_path=db,
        )


# ─── init_db creates session_meta ─────────────────────────────────────────────────────────────────────


def test_init_db_creates_session_meta_table(db: None):
    init_db(db_path=db)
    from agent.persistence.pg_store import _conn

    with _conn(db) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='session_meta'"
        ).fetchall()
    assert rows == [{"table_name": "session_meta"}]


# ─── get_session_meta defaults when row missing ────────────────────────────────────────────────


def test_get_session_meta_default_when_missing(db: Path):
    init_db(db_path=db)
    meta = get_session_meta(session_id="never-seen", db_path=db)
    assert meta == {
        "title": None,
        "pinned": False,
        "last_read_at": None,
    }


# ─── update_session_meta partial UPSERT ──────────────────────────────────────────────────────


def test_update_session_meta_partial_set(db: Path):
    init_db(db_path=db)
    update_session_meta(session_id="s1", fields={"title": "宠物"}, db_path=db)
    update_session_meta(session_id="s1", fields={"pinned": True}, db_path=db)
    meta = get_session_meta(session_id="s1", db_path=db)
    assert meta["title"] == "宠物"
    assert meta["pinned"] is True


def test_update_session_meta_coerces_bool_to_int(db: Path):
    init_db(db_path=db)
    update_session_meta(session_id="s1", fields={"pinned": True}, db_path=db)
    meta = get_session_meta(session_id="s1", db_path=db)
    assert meta["pinned"] is True


def test_update_session_meta_ignores_unknown_keys(db: Path):
    init_db(db_path=db)
    out = update_session_meta(
        session_id="s1", fields={"title": "ok", "hacker": "x"}, db_path=db
    )
    assert out["title"] == "ok"
    # unknown key silently dropped — no exception, no surface expansion


def test_update_session_meta_empty_fields_is_noop(db: Path):
    init_db(db_path=db)
    update_session_meta(session_id="s1", fields={"title": "keep"}, db_path=db)
    out = update_session_meta(session_id="s1", fields={}, db_path=db)
    assert out["title"] == "keep"


# ─── list_sessions JOIN ──────────────────────────────────────────────────────────────────────────


def test_list_sessions_includes_meta_defaults(db: Path):
    init_db(db_path=db)
    _seed_session(db, "s1")
    rows = list_sessions(db_path=db)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "s1"
    assert row["title"] is None
    assert row["pinned"] is False
    assert row["last_read_at"] is None


def test_list_sessions_returns_meta_per_row(db: Path):
    """session-delete-semantics：归档语义已删除——meta 只有 title/pinned/last_read_at。"""
    init_db(db_path=db)
    _seed_session(db, "s1")
    _seed_session(db, "s2")
    update_session_meta(session_id="s1", fields={"title": "Task A", "pinned": True}, db_path=db)
    rows = {r["session_id"]: r for r in list_sessions(db_path=db)}
    assert "s1" in rows and "s2" in rows
    assert rows["s1"]["pinned"] is True
    assert "archived" not in rows["s1"]


def test_list_sessions_pinned_first(db: Path):
    init_db(db_path=db)
    _seed_session(db, "s-old")
    _seed_session(db, "s-new")
    # s-old pinned — should appear first despite being older
    update_session_meta(session_id="s-old", fields={"pinned": True}, db_path=db)
    rows = list_sessions(db_path=db)
    assert rows[0]["session_id"] == "s-old"


# ─── cascade delete ──────────────────────────────────────────────────────────────────────────


def test_delete_session_events_cascades_meta(db: Path):
    init_db(db_path=db)
    _seed_session(db, "s1")
    update_session_meta(session_id="s1", fields={"title": "要删的"}, db_path=db)
    deleted = delete_session_events(session_id="s1", db_path=db)
    assert deleted == 3
    assert get_session_meta(session_id="s1", db_path=db) == {
        "title": None,
        "pinned": False,
        "last_read_at": None,
    }