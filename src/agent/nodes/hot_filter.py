"""hot_filter — pure rule pre-filter shared by the selection loop's tools.

The hard rules (no LLM — enforced red line) that decide whether a candidate
survives pre-filtering. The selection agent's ``search_1688`` tool applies
them per candidate and feeds the reason text back to the model.
"""
from __future__ import annotations

from ..state import ProductCandidate


# Tunable thresholds — see hot_filter.md. Hard rules first, LLM only fills
# in the human-readable reason text.
_BSR_TOP_PERCENT_MAX = 30.0
_MONTHLY_SEARCH_MIN = 1000
_GROWTH_MIN = -0.10


def _passes_rules(c: ProductCandidate) -> tuple[bool, str]:
    reasons = []
    if c.bsr_top_percent is not None and c.bsr_top_percent > _BSR_TOP_PERCENT_MAX:
        reasons.append(f"BSR Top {c.bsr_top_percent}% > 30% 阈值")
    if c.monthly_search is not None and c.monthly_search < _MONTHLY_SEARCH_MIN:
        reasons.append(f"月搜索量 {c.monthly_search} < 1000")
    if c.trend_growth is not None and c.trend_growth < _GROWTH_MIN:
        reasons.append(f"增长率 {c.trend_growth:.0%} < -10%")
    if reasons:
        return False, "淘汰: " + "; ".join(reasons)

    detail_parts = []
    if c.bsr_top_percent is not None:
        detail_parts.append(f"BSR Top {c.bsr_top_percent}%")
    if c.monthly_search is not None:
        detail_parts.append(f"月搜索量 {c.monthly_search:,}")
    if c.trend_growth is not None:
        detail_parts.append(f"增长 {c.trend_growth:+.0%}")
    if not detail_parts:
        detail_parts.append("数据不足，按边界处理放行")
    return True, "通过: " + ", ".join(detail_parts)
