"""Deterministic profit / price-gap math.

Pure Python, no LLM. Used by the selection loop's scoring tools.
"""
from __future__ import annotations

import math

from pydantic import BaseModel

# Public knobs (USD). Real values should be configurable per category via
# the future live attributes source (post-/0020 gate); these
# defaults match  §5.3 fixture shape and apply until then.
CNY_TO_USD = 0.14          # ~7 RMB per USD (rounded)
SHIPPING_RATE_USD_PER_KG = 8.0  # air-freight estimate
COMMISSION_RATE = 0.15     # Amazon referral fee default

# Amazon FBA fee table (USD) by approximate weight bucket. Conservative for
# small_standard / large_standard tiers; full calculator is out of scope for
# demo. Keys = lower bound in grams, inclusive.
_FBA_FEE_TABLE: list[tuple[int, float]] = [
    (0, 3.20),       # 0-100g  envelope / small
    (100, 3.86),     # 100-500g small standard
    (500, 5.55),     # 0.5-1kg
    (1000, 6.85),    # 1-1.5kg
    (1500, 7.95),    # 1.5-2kg large standard
    (2000, 9.40),
    (3000, 11.20),
]


class ProfitBreakdown(BaseModel):
    """All the money out, in, and net for one candidate."""

    source_price_cny: float
    target_price_usd: float
    weight_g: int

    landed_cost_usd: float        # 1688 price converted + freight
    freight_usd: float            # shipping estimate
    commission_usd: float         # Amazon referral fee
    fba_fee_usd: float            # Amazon FBA fulfilment fee
    net_profit_usd: float         # price - landed - commission - FBA
    profit_margin: float          # net / price (0-1)


def _fba_fee(weight_g: int) -> float:
    fee = _FBA_FEE_TABLE[0][1]
    for lower, f in _FBA_FEE_TABLE:
        if weight_g >= lower:
            fee = f
    return fee


def calculate_profit(
    *, source_price_cny: float, target_price_usd: float, weight_g: int
) -> ProfitBreakdown:
    """Deterministic profit calculation. Returned struct has exact USD values."""
    if weight_g <= 0:
        weight_g = 250  # sane default if data missing

    landed_cost_usd = source_price_cny * CNY_TO_USD
    freight_usd = (weight_g / 1000.0) * SHIPPING_RATE_USD_PER_KG
    commission_usd = target_price_usd * COMMISSION_RATE
    fba_fee_usd = _fba_fee(weight_g)

    net_profit_usd = target_price_usd - landed_cost_usd - freight_usd - commission_usd - fba_fee_usd
    profit_margin = net_profit_usd / target_price_usd if target_price_usd > 0 else 0.0

    return ProfitBreakdown(
        source_price_cny=source_price_cny,
        target_price_usd=target_price_usd,
        weight_g=weight_g,
        landed_cost_usd=round(landed_cost_usd, 4),
        freight_usd=round(freight_usd, 4),
        commission_usd=round(commission_usd, 4),
        fba_fee_usd=round(fba_fee_usd, 4),
        net_profit_usd=round(net_profit_usd, 4),
        profit_margin=round(profit_margin, 4),
    )


# ──── Price gap matching ────────────────────────────────────────────────────

# Threshold for "candidate is worth scoring" —  §4.1.
PRICE_GAP_RATIO_THRESHOLD = 1.5


def price_gap_ratio(source_price_cny: float, target_price_usd: float) -> float:
    """Source / target ratio expressed as 'target costs N× source'.

    A ratio of 3.5 means the Amazon price is 3.5× the landed 1688 cost in USD.
    """
    landed_usd = source_price_cny * CNY_TO_USD
    if landed_usd <= 0:
        return 0.0
    return target_price_usd / landed_usd


def match_price_gap(
    source_1688: list[dict], competitors: list[dict]
) -> list[dict]:
    """Pair 1688 products with best-matching Amazon competitor by category.

    Returns enriched rows ready to feed into ``ProductCandidate``: each row has
    ``source`` (1688 dict) + ``target_price_usd`` (matched competitor price) +
    ``competitor`` (best match) + ``price_gap_ratio``. Rows with ratio below
    ``PRICE_GAP_RATIO_THRESHOLD`` are dropped.
    """
    if not competitors:
        # Synthesize at 3× landed (ratio 3.0) so candidates stay in pipeline
        # with a neutral margin signal even when no Amazon data exists.
        return [_match_row(src, None) for src in source_1688]
    by_category: dict[str, list[dict]] = {}
    for c in competitors:
        by_category.setdefault(c.get("category", ""), []).append(c)

    out: list[dict] = []
    for src in source_1688:
        cand_competitors = by_category.get(src.get("category", ""), [])
        if cand_competitors:
            best = max(cand_competitors, key=lambda c: c.get("review_count", 0))
            matched = _match_row(src, best)
        else:
            matched = _match_row(src, None)
        if matched["price_gap_ratio"] >= PRICE_GAP_RATIO_THRESHOLD:
            out.append(matched)
    return out


def _match_row(src: dict, competitor: dict | None) -> dict:
    """Build a single matched row, synthesizing when no competitor exists."""
    cat = src.get("category", "")
    if competitor is None:
        target = round(src["price_cny"] * CNY_TO_USD * 3.0, 2)
        ratio = 3.0
    else:
        target = float(competitor.get("price_usd", 0))
        ratio = price_gap_ratio(src["price_cny"], target)
    return {
        "product_id": src["product_id"],
        "name_cn": src["name_cn"],
        "name_en": src.get("name_en", src["name_cn"]),
        "source_price_cny": float(src["price_cny"]),
        "target_price_usd": float(target),
        "price_gap_ratio": round(ratio, 2),
        "category": cat,
        "weight_g": int(src.get("weight_g", 0)) if src.get("weight_g") is not None else 250,
        # 语料竞品主图回退:1688 fixture 无图时用匹配竞品的真实主图
        "image_url": src.get("image_url") or (competitor or {}).get("image_url"),
        "_competitor": competitor,
    }