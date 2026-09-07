"""SEAM — memory graph: entity_edges 表 / 物化正确性 / supersede 双时间线 /
traverse 有界遍历 / 未知 rel 拒写。No LLM, no network, temp sqlite.
"""
from __future__ import annotations

import json

import pytest

from agent.agents.memory_graph import (
    EDGE_RELS,
    close_open_edges,
    ensure_entity_edges_table,
    materialize_edges_for_run,
    render_traverse_paths,
    traverse_edges,
    write_edge,
)
from agent.persistence.store import init_db, save_event, save_run


@pytest.fixture
def db(tmp_path):
    """Isolated temp sqlite with the memory-graph schema."""
    path = tmp_path / "mem.db"
    init_db(path)
    return path


def _env(db, run_id="r1", session_id="s1", keyword="宠物用品", decision="go",
         top_id="1688_004", candidates=None):
    """One persisted run (bound to the session via messages) + materialized
    edges — the messages row matters: supersedes looks runs up by session."""
    cands = candidates or [
        {
            "product_id": top_id,
            "name_cn": "喂食器",
            "total_score": 8.0,
            "recommendation": "go",
            "category": "pet_supplies",
            "_competitor": {"asin": "B0X004DDD"},
        }
    ]
    save_run(
        query="宠物用品",
        seed_keyword=keyword,
        decision=decision,
        report={
            "report": {"market_summary": "x"},
            "candidates": cands,
            "intent": {"seed_keyword": keyword},
        },
        run_id=run_id,
        db_path=db,
    )
    # messages row binds the run to the session (supersedes lookup joins this)
    save_event(
        session_id=session_id,
        run_id=run_id,
        event_type="user_message",
        sequence=0,
        payload={"query": "宠物用品"},
        db_path=db,
    )
    n = materialize_edges_for_run(
        run_id=run_id,
        session_id=session_id,
        keyword=keyword,
        decision=decision,
        report={"market_summary": "x"},
        candidates=cands,
        db_path=db,
    )
    return n


# ── schema / writes ─────────────────────────────────────────────────────────


def test_ensure_table_idempotent(db) -> None:
    ensure_entity_edges_table(db)
    ensure_entity_edges_table(db)  # no raise


def test_unknown_rel_rejected(db) -> None:
    with pytest.raises(ValueError, match="unknown edge rel"):
        write_edge(src_type="run", src_id="r1", rel="likes",
                   dst_type="product", dst_id="p1", db_path=db)


def test_unknown_entity_type_rejected(db) -> None:
    with pytest.raises(ValueError, match="unknown entity type"):
        write_edge(src_type="widget", src_id="w1", rel="decided",
                   dst_type="product", dst_id="p1", db_path=db)


def test_rel_enum_is_fixed_seven(db) -> None:
    assert EDGE_RELS == {
        "supersedes", "based_on", "matched_to", "in_category",
        "decided", "produced_listing", "competitor_of",
    }


# ── materialize ─────────────────────────────────────────────────────────────


def test_materialize_writes_decided_and_category_edges(db) -> None:
    n = _env(db)
    assert n >= 2
    decided = traverse_edges(entity_type="run", entity_id="r1", rel="decided", db_path=db)
    assert decided and decided[0]["dst_id"] == "1688_004"
    cats = traverse_edges(entity_type="product", entity_id="1688_004", rel="in_category", db_path=db)
    assert cats and cats[0]["dst_id"] == "pet_supplies"


def test_materialize_writes_based_on_for_competitor_snapshot(db) -> None:
    _env(db)
    based = traverse_edges(entity_type="product", entity_id="1688_004", rel="based_on", db_path=db)
    assert based and based[0]["dst_id"] == "B0X004DDD"


def test_supersede_chain_closes_prior_decided(db) -> None:
    """Second run under the same session+keyword supersedes the first; the
    first run's decided edge is closed (valid_until set) — 双时间线."""
    _env(db, run_id="r-old", session_id="s1", keyword="宠物用品", top_id="1688_002")
    first_edge = traverse_edges(entity_type="run", entity_id="r-old", rel="decided", db_path=db)
    assert first_edge and first_edge[0]["valid_until"] is None

    _env(db, run_id="r-new", session_id="s1", keyword="宠物用品", top_id="1688_004")
    supersedes = traverse_edges(entity_type="run", entity_id="r-new", rel="supersedes", db_path=db)
    assert supersedes and supersedes[0]["dst_id"] == "r-old", (
        f"supersedes missing; all edges="
        f"{traverse_edges(entity_type='run', entity_id='r-new', db_path=db)}"
    )
    # old decided edge now closed — the *raw* table keeps BOTH rows (closed
    # old + the re-opened replacement); the closed row carries valid_until
    # (双时间线). traverse_edges only surfaces open rows.
    from tests.conftest import TEST_DB_URL
    import psycopg

    with psycopg.connect(TEST_DB_URL, row_factory=psycopg.rows.dict_row) as conn:
        rows = conn.execute(
            "SELECT src_type, src_id, rel, dst_type, dst_id, valid_until "
            "FROM entity_edges WHERE src_type='run' AND src_id='r-old' AND rel='decided'"
        ).fetchall()
        rows = [dict(r) for r in rows]
    assert any(r["valid_until"] is not None for r in rows), f"old decided not closed: {rows}"
    # new decided edge open
    new = traverse_edges(entity_type="run", entity_id="r-new", rel="decided", db_path=db)
    assert new and new[0]["valid_until"] is None


def test_materialize_empty_candidates_writes_nothing(db) -> None:
    n = materialize_edges_for_run(
        run_id="r-empty", session_id="s1", keyword="宠物用品",
        decision="no-go", report=None, candidates=[], db_path=db,
    )
    assert n == 0


def test_materialize_empty_keyword_no_supersedes(db) -> None:
    """an empty seed_keyword must not chain unrelated runs of the
    same session into a supersedes edge (the "" equality used to match every
    prior run); keyword-independent edges (decided/in_category) still land."""
    _env(db, run_id="r-old", session_id="s1", keyword="宠物用品")
    _env(db, run_id="r-new", session_id="s1", keyword="")
    assert traverse_edges(entity_type="run", entity_id="r-new", rel="supersedes", db_path=db) == []
    decided = traverse_edges(entity_type="run", entity_id="r-new", rel="decided", db_path=db)
    assert decided and decided[0]["dst_id"] == "1688_004"


# ── traverse / render ───────────────────────────────────────────────────────


def test_traverse_hops_two_paths(db) -> None:
    """run --decided--> product --based_on--> amazon resolves in 2 hops."""
    _env(db)
    paths = traverse_edges(entity_type="run", entity_id="r1", hops=2, db_path=db)
    via = [p for p in paths if p.get("via") and p["via"]["dst_type"] == "amazon"]
    # the 2-hop row's own edge is run→product; the amazon leg is in via
    assert via and via[0]["dst_id"] == "1688_004"
    assert via[0]["via"]["rel"] == "based_on"
    assert via[0]["via"]["dst_id"] == "B0X004DDD"


def test_traverse_hops_boundary(db) -> None:
    with pytest.raises(ValueError, match="hops must be 1 or 2"):
        traverse_edges(entity_type="run", entity_id="r1", hops=3, db_path=db)


def test_render_paths_text(db) -> None:
    _env(db)
    text = render_traverse_paths(traverse_edges(entity_type="run", entity_id="r1", db_path=db))
    assert "run:r1 --decided--> product:1688_004" in text
    assert render_traverse_paths([]) == "(无相关记忆)"


def test_close_open_edges_returns_count(db) -> None:
    write_edge(src_type="product", src_id="p1", rel="decided",
               dst_type="run", dst_id="r9", db_path=db)
    assert close_open_edges(src_type="product", src_id="p1", rel="decided", db_path=db) == 1
    assert close_open_edges(src_type="product", src_id="p1", rel="decided", db_path=db) == 0
