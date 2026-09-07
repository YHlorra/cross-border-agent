"""PG-mode persistence tests — gated by PG_TEST_DATABASE_URL.

These exercise ``persistence/pg_store`` directly (the module the store.py
dispatcher re-exports when DATABASE_URL is set). Run:

    PG_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5433/crossborder \
        .venv/Scripts/python.exe -m pytest tests/test_pg_store.py -q

Covers tasks 2.1: write-one-read-one for every function family + pgvector
KNN ordering (L2) + dispatcher parity.
"""
from __future__ import annotations

import importlib
import os

import pytest

PG = os.environ.get("PG_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(not PG, reason="PG_TEST_DATABASE_URL not set"),
]


@pytest.fixture()
def pg_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point DATABASE_URL at the PG test database for pg_store calls."""
    monkeypatch.setenv("DATABASE_URL", PG or "")


@pytest.fixture()
def clean_pg(pg_env: None) -> None:  # noqa: ARG001
    """Truncate all tables so tests are order-independent."""
    from agent.persistence import pg_store

    with pg_store._conn(None) as conn:  # noqa: SLF001 — test-only access
        for table in (
            "messages",
            "session_meta",
            "listing_runs",
            "selection_runs",
            "entity_edges",
            "preference_embeddings",
        ):
            conn.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")
        conn.commit()


# ---- selection runs ---------------------------------------------------------


def test_selection_run_round_trip(clean_pg: None) -> None:
    from agent.persistence import pg_store

    rid = pg_store.save_run(
        query="宠物用品 预算2000",
        seed_keyword="宠物",
        decision="go",
        report={"report": {"x": 1}, "candidates": [{"name_cn": "A", "total_score": 8}]},
    )
    got = pg_store.get_run(rid)
    assert got is not None and got["decision"] == "go"
    assert got["candidates"][0]["name_cn"] == "A"
    assert any(r["id"] == rid for r in pg_store.list_runs(limit=5))


def test_listing_run_lifecycle(clean_pg: None) -> None:
    from agent.persistence import pg_store

    rid = pg_store.save_listing_run(
        session_id="s1",
        product_id="p1",
        market_code="US",
        listing={"listing": {"item_name": "宠物粘毛器"}, "candidate": {"name_en": "Roller"}},
    )
    got = pg_store.get_listing_run(rid)
    assert got is not None and got["listing_title"] == "宠物粘毛器"
    assert any(r["id"] == rid for r in pg_store.list_listing_runs(limit=5))
    renamed = pg_store.rename_listing_run(run_id=rid, title="我的草稿")
    assert renamed is not None and renamed["title"] == "我的草稿"
    assert pg_store.delete_listing_run(run_id=rid) is True
    assert pg_store.get_listing_run(rid) is None


def test_update_listing_field(clean_pg: None) -> None:
    from agent.persistence import pg_store

    rid = pg_store.save_listing_run(
        session_id="s1",
        product_id="p1",
        market_code="US",
        listing={"item_name": "旧名"},
    )
    pg_store.update_listing_field(rid, "item_name", "新名")
    assert pg_store.get_listing_run(rid)["listing"]["item_name"] == "新名"
    with pytest.raises(ValueError):
        pg_store.update_listing_field("missing", "item_name", "x")


# ---- messages / sessions ----------------------------------------------------


def test_message_round_trip_and_session_listing(clean_pg: None) -> None:
    from agent.persistence import pg_store

    pg_store.save_event(
        session_id="sess-1", run_id="run-1", event_type="user_message",
        sequence=0, payload={"query": "q"},
    )
    pg_store.save_event(
        session_id="sess-1", run_id="run-1", event_type="final",
        sequence=1, payload={"decision": "go"},
    )
    events = pg_store.list_session_events(session_id="sess-1")
    assert [e["sequence"] for e in events] == [0, 1]
    runs = pg_store.list_session_runs(session_id="sess-1")
    assert runs[0]["decision"] == "go" and runs[0]["event_count"] == 2
    sessions = pg_store.list_sessions()
    assert sessions[0]["session_id"] == "sess-1" and sessions[0]["event_count"] == 2
    # include_failed filter
    pg_store.mark_run_failed(run_id="run-1")
    assert len(pg_store.list_session_events(session_id="sess-1", include_failed=False)) == 0


def test_legacy_message_wrappers(clean_pg: None) -> None:
    from agent.persistence import pg_store

    mid = pg_store.save_message("sess-2", "user", "你好")
    assert mid
    rows = pg_store.list_messages("sess-2")
    assert rows[0]["role"] == "user" and rows[0]["content"] == "你好"


def test_session_meta_upsert(clean_pg: None) -> None:
    from agent.persistence import pg_store

    pg_store.update_session_meta(session_id="sess-3", fields={"title": "T", "pinned": True})
    meta = pg_store.get_session_meta(session_id="sess-3")
    assert meta["title"] == "T" and meta["pinned"] is True
    pg_store.update_session_meta(session_id="sess-3", fields={"title": "T2"})
    meta = pg_store.get_session_meta(session_id="sess-3")
    assert meta["title"] == "T2" and meta["pinned"] is True  # 保留未提及键
    assert pg_store.delete_session_events(session_id="sess-3") >= 0


# ---- preferences + pgvector -------------------------------------------------


def _vec(seed: int, dim: int = 1536) -> list[float]:
    out = [0.0] * dim
    out[seed % dim] = 1.0
    return out


def test_preference_lifecycle_and_knn(clean_pg: None) -> None:
    from agent.persistence import pg_store

    pid_near = pg_store.insert_preference(
        category="style",
        preference_text="near",
        embedding=_vec(1),
        source_run_id="r",
        source_session_id="s",
    )
    pid_far = pg_store.insert_preference(
        category="style",
        preference_text="far",
        embedding=_vec(1000),
        source_run_id="r",
        source_session_id="s",
    )
    knn = pg_store.knn_preferences(_vec(1), 2)
    assert knn[0][0] == pid_near and knn[0][1] <= knn[1][1]
    active = pg_store.list_active_preferences(category="style")
    assert {p["id"] for p in active} == {pid_near, pid_far}
    assert pg_store.reinforce_preference(preference_id=pid_near) == 1
    pg_store.supersede_preference(old_id=pid_far, new_id=pid_near)
    assert len(pg_store.list_active_preferences(category="style")) == 1
    assert pg_store.delete_preference(preference_id=pid_near) == 1


def test_dispatch_reexports_pg_backend(clean_pg: None, monkeypatch) -> None:
    """The store.py dispatcher flips to pg_store when DATABASE_URL is set."""
    import agent.persistence.store as store_mod

    monkeypatch.setenv("DATABASE_URL", PG or "")
    reloaded = importlib.reload(store_mod)
    from agent.persistence import pg_store

    try:
        assert reloaded.save_event.__module__ == "agent.persistence.pg_store"
        assert reloaded.save_event is not None
        assert pg_store.init_db is not None
    finally:
        monkeypatch.delenv("DATABASE_URL")
        importlib.reload(store_mod)
