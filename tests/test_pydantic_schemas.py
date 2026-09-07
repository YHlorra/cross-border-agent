"""Pydantic schema shapes — no LLM, no network."""
from __future__ import annotations

from agent.state import (
    DimensionScore,
    ProductCandidate,
    ScoredCandidate,
    SelectionReport,
)


def test_product_candidate_minimum_fields():
    c = ProductCandidate(
        product_id="1",
        name_cn="耳机",
        name_en="earbuds",
        source_price_cny=10.0,
        target_price_usd=30.0,
        price_gap_ratio=3.0,
        category="audio",
    )
    assert c.product_id == "1"
    assert c.weight_g is None
    assert c.review_count == 0


def test_scored_candidate_inherits_fields():
    base = ProductCandidate(
        product_id="2",
        name_cn="x",
        name_en="x",
        source_price_cny=10.0,
        target_price_usd=30.0,
        price_gap_ratio=3.0,
        category="c",
    )
    s = ScoredCandidate(
        **base.model_dump(),
        scores=[
            DimensionScore(dimension="market_demand", score=8.0, reason="top"),
        ],
        total_score=8.0,
        recommendation="go",
    )
    assert s.product_id == "2"
    assert s.scores[0].score == 8.0
    assert s.recommendation == "go"


def test_selection_report_round_trip():
    r = SelectionReport(
        session_id="t1",
        seed_keyword="earbuds",
        market_summary="market is rising",
    )
    j = r.model_dump_json()
    r2 = SelectionReport.model_validate_json(j)
    assert r2.seed_keyword == "earbuds"