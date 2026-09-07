"""get_run reading exit + legacy row tolerance .

Pure DB SEAM — no LLM, no network. Covers the full envelope, legacy rows
saved before candidates/intent were tracked, missing rows, and corrupted
report_json. Degradation is part of the contract (R3-aligned: surface what
is true, never invent).
"""

from __future__ import annotations

import json
from agent.persistence import get_run, save_run
from tests.conftest import TEST_DB_URL


def _connect():
    """PG-only：直连测试库造数（db_path 被 PG 后端忽略）。
    隔离由 conftest autouse TRUNCATE 提供。"""
    import psycopg

    return psycopg.connect(TEST_DB_URL, row_factory=psycopg.rows.dict_row)


def test_get_run_full_envelope(tmp_path: Path):
    db = tmp_path / "memory.db"
    rid = save_run(
        query="宠物用品",
        seed_keyword="猫粮",
        decision="go",
        report={
            "report": {
                "session_id": "t1",
                "seed_keyword": "猫粮",
                "market_summary": "...",
                "top_recommendations": [],
            },
            "candidates": [
                {"name_cn": "猫粮A", "total_score": 8.0, "name_en": "cat food A"},
                {"name_cn": "猫粮B", "total_score": 6.0, "name_en": "cat food B"},
            ],
            "intent": {"budget_cny": 2000.0, "preferences": ["low_price"]},
        },
        db_path=None,
    )
    row = get_run(rid, db_path=None)
    assert row is not None
    assert row["id"] == rid
    assert row["query"] == "宠物用品"
    assert row["seed_keyword"] == "猫粮"
    assert row["decision"] == "go"
    assert row["report"]["session_id"] == "t1"
    assert len(row["candidates"]) == 2
    assert row["candidates"][0]["name_cn"] == "猫粮A"
    assert row["intent"]["budget_cny"] == 2000.0
    assert row["intent"]["preferences"] == ["low_price"]


def test_get_run_legacy_row_no_candidates_no_intent():
    """Rows saved before candidates/intent were persisted: bare report dict.

    Frontend degrades gracefully — top/intent lines hide for these.
    """
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO selection_runs
               (id, query, seed_keyword, decision, report_json, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                "legacy-1",
                "旧查询",
                "",
                "go",
                json.dumps(
                    {
                        "session_id": "t1",
                        "seed_keyword": "kw",
                        "market_summary": "...",
                        "top_recommendations": [],
                    },
                    ensure_ascii=False,
                ),
                "2026-08-01T00:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    row = get_run("legacy-1", db_path=None)
    assert row is not None
    assert row["id"] == "legacy-1"
    assert row["candidates"] == []
    assert row["intent"] is None
    assert row["report"] is not None
    assert row["report"]["session_id"] == "t1"


def test_get_run_not_found():
    assert get_run("does-not-exist", db_path=None) is None


def test_get_run_malformed_json():
    """Corrupted report_json → empty envelope fields, no throw."""
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO selection_runs
               (id, query, seed_keyword, decision, report_json, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            ("bad-1", "x", "", "", "not-json-{", "2026-08-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    row = get_run("bad-1", db_path=None)
    assert row is not None
    assert row["report"] is None
    assert row["candidates"] == []
    assert row["intent"] is None