"""偏好 RAG SEAM（pure DB + mock embed，no LLM）。

覆盖：
- mock_embed 确定性（同 text → 同 vector；不同 text → 几乎正交）
- insert_preference + list_active_preferences round-trip
- reinforce_preference 累加 + 时间戳更新
- supersede_preference 软删 + chain
- KNN 检索：mock embed → insert → retrieve top-k → score 排序
- decay.score 边界：rc=0、age=0、distance=0
- expires_at 软过期
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.persistence import (
    init_db,
    insert_preference,
    list_active_preferences,
    reinforce_preference,
    supersede_preference,
)
from agent.preferences.decay import score, age_days_from
from agent.preferences.embed import mock_embed
from agent.preferences.retrieve import retrieve
from agent.preferences.types import NewPreference, PreferenceHit, VALID_CATEGORIES


@pytest.fixture
def db(tmp_path: Path) -> str:
    return str(tmp_path / "prefs.db")


# ─── mock_embed ─────────────────────────────────────────────────────────────


def test_mock_embed_deterministic():
    a = mock_embed("宠物 1000 以内", dimensions=64)
    b = mock_embed("宠物 1000 以内", dimensions=64)
    assert a == b
    assert len(a) == 64


def test_mock_embed_different_texts_near_orthogonal():
    a = mock_embed("猫粮", dimensions=128)
    b = mock_embed("汽车配件", dimensions=128)
    # different random-ish seeds → cosine should be low
    dot = sum(x * y for x, y in zip(a, b))
    assert abs(dot) < 0.5  # very loose; 4-byte SHA blocks


def test_mock_embed_unit_norm():
    a = mock_embed("test", dimensions=64)
    norm_sq = sum(x * x for x in a)
    assert abs(norm_sq - 1.0) < 1e-9


def test_mock_embed_dimensions_argument():
    assert len(mock_embed("x", dimensions=16)) == 16
    assert len(mock_embed("x", dimensions=1536)) == 1536
    with pytest.raises(ValueError):
        mock_embed("x", dimensions=0)


# ─── store: insert / list / reinforce / supersede ──────────────────────────


def test_insert_preference_round_trip(db: str):
    emb = mock_embed("想做 1000 以内的母婴")
    pid = insert_preference(
        category="budget",
        preference_text="想做 1000 以内的母婴",
        embedding=emb,
        source_run_id="r1",
        source_session_id="s1",
        db_path=db,
    )
    assert isinstance(pid, int) and pid > 0
    rows = list_active_preferences(db_path=db)
    assert len(rows) == 1
    assert rows[0]["category"] == "budget"
    assert rows[0]["reinforcement_count"] == 1


def test_reinforce_preference_increments(db: str):
    emb = mock_embed("x")
    pid = insert_preference(
        category="category", preference_text="母婴",
        embedding=emb, source_run_id="r1", source_session_id="s1", db_path=db,
    )
    n1 = reinforce_preference(preference_id=pid, db_path=db)
    n2 = reinforce_preference(preference_id=pid, db_path=db)
    assert n1 == 1 and n2 == 1
    rows = list_active_preferences(db_path=db)
    assert rows[0]["reinforcement_count"] == 3  # initial 1 + 2 reinforces


def test_supersede_preference_marks_inactive_and_chain(db: str):
    emb_a = mock_embed("母婴类")
    emb_b = mock_embed("母婴 + 童装")
    old = insert_preference(
        category="category", preference_text="母婴",
        embedding=emb_a, source_run_id="r1", source_session_id="s1", db_path=db,
    )
    new = insert_preference(
        category="category", preference_text="母婴 + 童装",
        embedding=emb_b, source_run_id="r2", source_session_id="s2", db_path=db,
    )
    supersede_preference(old_id=old, new_id=new, db_path=db)
    active = list_active_preferences(db_path=db)
    assert len(active) == 1
    assert active[0]["id"] == new
    # chain recorded（PG-only：经 pg_store 同连接读回）
    from agent.persistence.pg_store import _conn

    with _conn(db) as conn:
        row = conn.execute(
            "SELECT superseded_by, active FROM preference_embeddings WHERE id=%s",
            (old,),
        ).fetchone()
    assert row["superseded_by"] == new
    assert row["active"] == 0


def test_expires_at_filters_inactive(db: str):
    emb = mock_embed("x")
    insert_preference(
        category="platform", preference_text="Amazon US",
        embedding=emb, source_run_id="r1", source_session_id="s1",
        expires_at="2000-01-01T00:00:00+00:00",  # long past
        db_path=db,
    )
    assert list_active_preferences(db_path=db) == []


def test_list_active_filters_by_category(db: str):
    emb = mock_embed("x")
    insert_preference(
        category="budget", preference_text="¥2000",
        embedding=emb, source_run_id="r1", source_session_id="s1", db_path=db,
    )
    insert_preference(
        category="category", preference_text="母婴",
        embedding=emb, source_run_id="r1", source_session_id="s1", db_path=db,
    )
    budgets = list_active_preferences(category="budget", db_path=db)
    assert len(budgets) == 1
    assert budgets[0]["category"] == "budget"


# ─── decay.score ────────────────────────────────────────────────────────────


def test_score_perfect_similarity_zero_age_zero_distance():
    # distance=0 → similarity=1; rc=0 → reinforce=1+log1p(0)=1; age=0 → decay=1
    assert score(0.0, 0, 0.0) == 1.0


def test_score_huge_distance_low_similarity():
    s = score(1_000_000.0, 0, 0.0)
    assert s < 1e-5


def test_score_reinforcement_bonus():
    s1 = score(1.0, 1, 0.0)  # 0.5 * 1.69 * 1.0
    s5 = score(1.0, 5, 0.0)
    s50 = score(1.0, 50, 0.0)
    assert s5 > s1
    assert s50 > s5


def test_score_age_decay_halflife():
    """Half-life applies to the decay factor only, not the whole score."""
    import math
    s0 = score(1.0, 1, 0.0)
    s30 = score(1.0, 1, 30.0)  # default half_life
    s60 = score(1.0, 1, 60.0)
    # decay = exp(-age/half_life); sX = s0 * exp(-age/30)
    # s30/s0 = exp(-1) ≈ 0.3679
    assert abs(s30 / s0 - math.exp(-1)) < 1e-9
    # s60/s0 = exp(-2) ≈ 0.1353
    assert abs(s60 / s0 - math.exp(-2)) < 1e-9
    # strict ordering
    assert s0 > s30 > s60


def test_age_days_from_recent():
    from datetime import datetime, timezone, timedelta

    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    assert 1.9 < age_days_from(recent) < 2.1


# ─── retrieve: KNN + decay 排序 ───────────────────────────────────────────


def test_retrieve_returns_top_k_ordered_by_score(db: str):
    """All hits returned; scores monotonically non-increasing; identity-by-text
    invariant: a preference with the same embedding as the query is exactly
    the top hit. (mock_embed is deterministic — querying with the same text
    as the stored preference's preference_text gives distance=0.)"""
    e1 = mock_embed("想要 猫粮 类")
    e2 = mock_embed("完全不同的商品类别 母婴用品")
    insert_preference(
        category="category", preference_text="想要 猫粮 类",
        embedding=e1, source_run_id="r1", source_session_id="s1", db_path=db,
    )
    insert_preference(
        category="category", preference_text="完全不同的商品类别 母婴用品",
        embedding=e2, source_run_id="r2", source_session_id="s2", db_path=db,
    )
    q = mock_embed("想要 猫粮 类")
    hits = retrieve(query_embedding=q, top_k=2, db_path=db)
    assert len(hits) == 2
    # monotonic non-increasing scores
    assert hits[0].score >= hits[1].score
    # exact-match embedding is the top hit (distance=0 → score=1)
    assert hits[0].preference_text == "想要 猫粮 类"
    assert hits[0].distance == 0.0


def test_retrieve_excludes_superseded(db: str):
    emb_a = mock_embed("母婴")
    emb_b = mock_embed("母婴 + 童装")
    old = insert_preference(
        category="category", preference_text="母婴",
        embedding=emb_a, source_run_id="r1", source_session_id="s1", db_path=db,
    )
    new = insert_preference(
        category="category", preference_text="母婴 + 童装",
        embedding=emb_b, source_run_id="r2", source_session_id="s2", db_path=db,
    )
    supersede_preference(old_id=old, new_id=new, db_path=db)
    q = mock_embed("母婴")
    hits = retrieve(query_embedding=q, top_k=10, db_path=db)
    assert all(h.id != old for h in hits)


def test_retrieve_excludes_expired(db: str):
    emb = mock_embed("x")
    insert_preference(
        category="platform", preference_text="Amazon US",
        embedding=emb, source_run_id="r1", source_session_id="s1",
        expires_at="2000-01-01T00:00:00+00:00",  # expired
        db_path=db,
    )
    q = mock_embed("Amazon")
    hits = retrieve(query_embedding=q, top_k=5, db_path=db)
    assert hits == []


def test_retrieve_empty_query_returns_empty(db: str):
    assert retrieve(query_embedding=[], db_path=db) == []


def test_retrieve_category_filter(db: str):
    emb = mock_embed("x")
    insert_preference(
        category="budget", preference_text="1000",
        embedding=emb, source_run_id="r1", source_session_id="s1", db_path=db,
    )
    insert_preference(
        category="logistics", preference_text="快",
        embedding=emb, source_run_id="r1", source_session_id="s1", db_path=db,
    )
    q = mock_embed("x")
    hits_b = retrieve(query_embedding=q, top_k=10, category="budget", db_path=db)
    assert len(hits_b) == 1
    assert hits_b[0].category == "budget"


# ─── types ─────────────────────────────────────────────────────────────────


def test_valid_categories():
    assert "budget" in VALID_CATEGORIES
    assert "category" in VALID_CATEGORIES
    assert "logistics" in VALID_CATEGORIES
    assert "platform" in VALID_CATEGORIES


def test_new_preference_construction():
    p = NewPreference(
        category="budget", preference_text="1000", confidence=0.8, supersedes_id=None
    )
    assert p.confidence == 0.8


def test_preference_hit_construction():
    h = PreferenceHit(
        id=1, category="budget", preference_text="1000",
        distance=0.5, reinforcement_count=1, age_days=1.0, score=0.5,
    )
    assert h.id == 1
