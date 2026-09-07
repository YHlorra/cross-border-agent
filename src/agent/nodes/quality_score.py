"""quality_score — shared scoring contract used by the selection loop's tools.

Five-dimension scoring schema + post-processing (sanitize) that the loop's
``score_candidates`` tool applies to every LLM verdict before it reaches the
report. The 1-10 clamp and the data-insufficient cap
structured output is never trusted as-is.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..state import DimensionScore, ProductCandidate


# Weights from  §2.1 — must sum to 1.0.
WEIGHTS: dict[str, float] = {
    "market_demand": 0.25,
    "competition": 0.20,
    "profit": 0.30,
    "seasonality": 0.10,
    "repurchase": 0.15,
}


def _weighted_total(scores: list[DimensionScore]) -> float:
    if not scores:
        return 0.0
    total = 0.0
    for s in scores:
        total += s.score * WEIGHTS.get(s.dimension, 0.0)
    return round(total, 1)


def _recommendation(total: float) -> str:
    if total >= 7.5:
        return "go"
    if total >= 6.0:
        return "caution"
    return "no-go"


class QualityScoreOutput(BaseModel):
    """Schema the LLM returns per candidate."""

    product_id: str
    market_demand: DimensionScore
    competition: DimensionScore
    profit: DimensionScore
    seasonality: DimensionScore
    repurchase: DimensionScore
    opportunities: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


# Candidate fields each dimension is judged from. A dimension whose fields are
# ALL missing (None, or 0 for counts) is "数据不足" and must not score above 5.
_DIMENSION_FIELDS: dict[str, tuple[str, ...]] = {
    "market_demand": ("monthly_search", "bsr_top_percent"),
    "competition": ("review_count",),
    "profit": ("source_price_cny", "target_price_usd"),
    "seasonality": ("trend_growth",),
    "repurchase": (),
}

_DATA_INSUFFICIENT_TAG = "[数据不足]"
_INSUFFICIENT_CAP = 5.0


def _field_missing(value: object) -> bool:
    return value is None or value == 0


def sanitize_scores(
    output: QualityScoreOutput, candidate: ProductCandidate
) -> list[DimensionScore]:
    """Post-process LLM dimension scores before the weighted total.

    LLM structured output is not trusted: clamp scores to
    1-10, and cap data-insufficient dimensions at 5 with a visible tag so the
    report never presents speculation as a confident score.
    """
    raw: dict[str, DimensionScore] = {
        "market_demand": output.market_demand,
        "competition": output.competition,
        "profit": output.profit,
        "seasonality": output.seasonality,
        "repurchase": output.repurchase,
    }
    result: list[DimensionScore] = []
    for dim, ds in raw.items():
        score = max(1.0, min(10.0, ds.score))
        reason = ds.reason
        fields = _DIMENSION_FIELDS[dim]
        insufficient = all(_field_missing(getattr(candidate, f, None)) for f in fields)
        if insufficient:
            score = min(score, _INSUFFICIENT_CAP)
            if not reason.startswith(_DATA_INSUFFICIENT_TAG):
                reason = f"{_DATA_INSUFFICIENT_TAG} {reason}"
        result.append(
            DimensionScore(dimension=dim, score=score, reason=reason)
        )
    return result
