"""Pydantic models for the selection agent.

Field names mirror the TypeScript types in `lib/selection-types.ts`. Keep the two
in lock-step when adding / renaming fields.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ──── Pydantic models ─────────────────────────────────────────────────────────


class ProductCandidate(BaseModel):
    """Single 1688 product, optionally enriched with Amazon competitor info."""

    product_id: str
    name_cn: str
    name_en: str
    source_price_cny: float
    target_price_usd: float
    price_gap_ratio: float
    category: str
    bsr_rank: Optional[int] = None
    bsr_top_percent: Optional[float] = None
    review_count: int = 0
    monthly_search: Optional[int] = None
    trend_growth: Optional[float] = None
    trend_series: list[float] = Field(default_factory=list)
    weight_g: Optional[int] = None
    image_url: Optional[str] = None


class DimensionScore(BaseModel):
    """One of the five quality-scoring dimensions for a candidate."""

    dimension: str  # market_demand | competition | profit | seasonality | repurchase
    score: float  # 1-10
    reason: str


class ScoredCandidate(ProductCandidate):
    """A candidate with quality scores attached."""

    scores: list[DimensionScore] = Field(default_factory=list)
    total_score: float = 0.0
    recommendation: str = "no-go"  # go | caution | no-go
    opportunities: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class IntentPlan(BaseModel):
    """Structured intent for one selection run (wire/replay envelope shape).

    ``preferences`` uses canonical english keys (``light`` / ``repurchase`` /
    ``low_price``); budget and preferences are optional context the agent may
    honor when scoring.
    """

    seed_keyword: str
    category_hint: Optional[str] = None
    budget_cny: Optional[float] = None
    min_price_cny: Optional[float] = None
    preferences: list[str] = Field(default_factory=list)
    confidence: str = "low"  # high | low
    rationale: str = ""


class CategoryTrend(BaseModel):
    """Category-level trend row (PG ``corpus_categories`` mock / future live trend source)."""

    category: str
    monthly_searches: list[float] = Field(default_factory=list)
    growth_rate_90d: float = 0.0
    seasonality: str = ""
    peak_months: list[int] = Field(default_factory=list)


class HotFilterVerdict(BaseModel):
    """Verdict for a single candidate from the hot-filter step."""

    product_id: str
    passed: bool
    filter_reason: str


class DetailedAnalysis(BaseModel):
    """Top-N deep-dive (opportunity / risk / cut-in) in the run report."""

    product_id: str
    opportunities: list[str]
    risks: list[str]
    cut_in_direction: str


class SelectionReport(BaseModel):
    """Final report streamed out at the end of a run."""

    session_id: str
    seed_keyword: str
    market_summary: str
    top_recommendations: list[DetailedAnalysis] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)