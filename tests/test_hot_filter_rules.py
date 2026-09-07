"""Hot-filter rule pre-filtering — no LLM."""
from __future__ import annotations

from agent.nodes.hot_filter import _passes_rules
from agent.state import ProductCandidate


def make(**overrides) -> tuple[bool, str]:
    base = dict(
        product_id="x",
        name_cn="X",
        name_en="X",
        source_price_cny=10.0,
        target_price_usd=50.0,
        price_gap_ratio=5.0,
        category="c",
    )
    base.update(overrides)
    c = ProductCandidate(**base)
    return _passes_rules(c)


def test_passes_with_good_signals():
    ok, reason = make(bsr_top_percent=10.0, monthly_search=5000, trend_growth=0.20)
    assert ok is True
    assert "BSR Top 10.0%" in reason


def test_drops_bsr_too_low_rank():
    ok, reason = make(bsr_top_percent=45.0, monthly_search=5000, trend_growth=0.10)
    assert ok is False
    assert "BSR Top 45.0%" in reason


def test_drops_low_monthly_search():
    ok, reason = make(bsr_top_percent=10.0, monthly_search=500, trend_growth=0.10)
    assert ok is False
    assert "月搜索量 500" in reason


def test_drops_negative_growth():
    ok, reason = make(bsr_top_percent=10.0, monthly_search=5000, trend_growth=-0.20)
    assert ok is False
    assert "增长率" in reason


def test_passes_when_signals_missing():
    """All key signals absent → rule cannot reject → passes with '数据不足'."""
    ok, reason = make()
    assert ok is True
    assert "数据不足" in reason


def test_passes_at_threshold():
    ok, _ = make(bsr_top_percent=30.0, monthly_search=1000, trend_growth=-0.10)
    assert ok is True