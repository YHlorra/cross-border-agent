"""SEAM-1 tests: sanitize_scores — post-processing of LLM dimension scores.

LLM structured output cannot be trusted (plan risk 9.4): scores can leave the
1-10 range, and dimensions whose underlying data fields are missing must not
score above 5. This pure function is the enforcement layer between the LLM and
the weighted total. No LLM involved.
"""
from __future__ import annotations

from agent.nodes.quality_score import DimensionScore, QualityScoreOutput, sanitize_scores
from agent.state import ProductCandidate


def _candidate(**overrides: object) -> ProductCandidate:
    base: dict = {
        "product_id": "1688_004",
        "name_cn": "宠物自动喂食器",
        "name_en": "Smart Pet Feeder",
        "source_price_cny": 198.0,
        "target_price_usd": 39.99,
        "price_gap_ratio": 1.44,
        "category": "pet_supplies",
        "bsr_rank": 8500,
        "bsr_top_percent": 8.0,
        "review_count": 210,
        "monthly_search": 32000,
        "trend_growth": 0.18,
    }
    base.update(overrides)
    return ProductCandidate(**base)  # type: ignore[arg-type]


def _output(**dims: float) -> QualityScoreOutput:
    def dim(score: float) -> DimensionScore:
        return DimensionScore(dimension="x", score=score, reason="基于数据的理由")

    defaults = {
        "market_demand": 8.0,
        "competition": 7.0,
        "profit": 9.0,
        "seasonality": 6.0,
        "repurchase": 7.0,
    }
    defaults.update(dims)
    return QualityScoreOutput(
        product_id="1688_004",
        market_demand=dim(defaults["market_demand"]),
        competition=dim(defaults["competition"]),
        profit=dim(defaults["profit"]),
        seasonality=dim(defaults["seasonality"]),
        repurchase=dim(defaults["repurchase"]),
    )


def _by_dim(scores: list[DimensionScore]) -> dict[str, DimensionScore]:
    return {s.dimension: s for s in scores}


def test_scores_clamped_to_1_10() -> None:
    scores = sanitize_scores(_output(market_demand=12.5, competition=-2), _candidate())
    by = _by_dim(scores)
    assert by["market_demand"].score == 10
    assert by["competition"].score == 1


def test_missing_market_data_caps_score_at_5() -> None:
    cand = _candidate(monthly_search=None, bsr_top_percent=None)
    scores = sanitize_scores(_output(market_demand=9), cand)
    md = _by_dim(scores)["market_demand"]
    assert md.score == 5
    assert md.reason.startswith("[数据不足]")


def test_present_market_data_keeps_score() -> None:
    scores = sanitize_scores(_output(market_demand=9), _candidate())
    md = _by_dim(scores)["market_demand"]
    assert md.score == 9
    assert not md.reason.startswith("[数据不足]")


def test_zero_counts_count_as_missing() -> None:
    # A 0 review_count is indistinguishable from unknown (no competitor
    # matched), so competition is capped.
    cand = _candidate(review_count=0)
    scores = sanitize_scores(_output(competition=8), cand)
    comp = _by_dim(scores)["competition"]
    assert comp.score == 5
    assert comp.reason.startswith("[数据不足]")


def test_repurchase_always_insufficient_until_attributes_wired() -> None:
    # category_attributes.json (repurchase_rate_level) is not wired into the
    # scoring payload — no candidate field backs this dimension.
    scores = sanitize_scores(_output(repurchase=8), _candidate())
    rep = _by_dim(scores)["repurchase"]
    assert rep.score == 5
    assert rep.reason.startswith("[数据不足]")


def test_profit_with_prices_present_not_capped() -> None:
    scores = sanitize_scores(_output(profit=9), _candidate())
    assert _by_dim(scores)["profit"].score == 9


def test_no_double_prefix_when_llm_already_noted() -> None:
    out = _output(seasonality=7)
    out.seasonality.reason = "[数据不足] 淡旺季数据缺失"
    cand = _candidate(trend_growth=None)
    scores = sanitize_scores(out, cand)
    assert _by_dim(scores)["seasonality"].reason == "[数据不足] 淡旺季数据缺失"


def test_below_5_insufficient_score_not_raised() -> None:
    cand = _candidate(monthly_search=None, bsr_top_percent=None)
    scores = sanitize_scores(_output(market_demand=3), cand)
    assert _by_dim(scores)["market_demand"].score == 3
