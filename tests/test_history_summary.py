"""SEAM tests: top-candidate summary extraction from persisted runs.

The sidebar shows each history entry's top candidate (name + total score).
Extraction is a pure decision over the stored report_json — pinned here so the
JSON envelope shape (report + candidates) and legacy-run tolerance cannot
regress. No LLM involved.
"""
from __future__ import annotations

import json

from agent.persistence.pg_store import _top_summary
from agent.persistence.store import list_runs, save_run


def _envelope(candidates: list[dict]) -> str:
    return json.dumps({"report": {"market_summary": "x"}, "candidates": candidates})


def test_extracts_top_candidate_by_total_score() -> None:
    raw = _envelope(
        [
            {"name_cn": "耳机A", "total_score": 7.2},
            {"name_cn": "喂食器B", "total_score": 8.4},
            {"name_cn": "瑜伽垫C", "total_score": 6.1},
        ]
    )
    top = _top_summary(raw)
    assert top == {"top_name": "喂食器B", "top_score": 8.4}


def test_legacy_report_json_without_candidates_returns_none() -> None:
    legacy = json.dumps({"market_summary": "x", "top_recommendations": []})
    assert _top_summary(legacy) is None


def test_none_and_invalid_json_return_none() -> None:
    assert _top_summary(None) is None
    assert _top_summary("not json {") is None


def test_empty_candidate_list_returns_none() -> None:
    assert _top_summary(_envelope([])) is None


def test_list_runs_returns_top_summary(tmp_path) -> None:
    db = tmp_path / "memory.db"
    save_run(
        query="宠物用品",
        seed_keyword="宠物用品",
        decision="caution",
        report={
            "report": {"market_summary": "m"},
            "candidates": [
                {"name_cn": "宠物喂食器", "total_score": 7.1},
            ],
        },
        db_path=db,
    )
    rows = list_runs(db_path=db)
    assert len(rows) == 1
    assert rows[0]["top"] == {"top_name": "宠物喂食器", "top_score": 7.1}
    # the raw report_json must not leak into the list payload
    assert "report_json" not in rows[0]
