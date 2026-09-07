"""Deterministic profit math — no LLM."""
from __future__ import annotations

from agent.tools.calc_tools import (
    CNY_TO_USD,
    COMMISSION_RATE,
    SHIPPING_RATE_USD_PER_KG,
    calculate_profit,
    match_price_gap,
    price_gap_ratio,
)


def test_profit_bone_conduction_earbuds():
    # From the  fixture: 48 CNY, $32.99, 95g
    p = calculate_profit(source_price_cny=48.0, target_price_usd=32.99, weight_g=95)
    assert p.landed_cost_usd == round(48.0 * CNY_TO_USD, 4)
    assert p.freight_usd == round(0.095 * SHIPPING_RATE_USD_PER_KG, 4)
    assert p.commission_usd == round(32.99 * COMMISSION_RATE, 4)
    assert p.fba_fee_usd == 3.20  # weight < 100g tier
    expected_net = 32.99 - p.landed_cost_usd - p.freight_usd - p.commission_usd - p.fba_fee_usd
    assert p.net_profit_usd == round(expected_net, 4)
    assert p.profit_margin > 0.50  # net 17.36 / price 32.99 ≈ 52.6%
def test_profit_heavy_pet_feeder():
    p = calculate_profit(source_price_cny=198.0, target_price_usd=89.99, weight_g=1500)
    assert p.landed_cost_usd == round(198.0 * CNY_TO_USD, 4)
    assert p.fba_fee_usd == 7.95  # 1500-2000g tier
    assert p.freight_usd == round(1.5 * SHIPPING_RATE_USD_PER_KG, 4)
    # 27.72 landed + 12 freight + 13.50 commission + 7.95 FBA = 61.17 → 28.82 net
    assert p.net_profit_usd > 25
    assert p.net_profit_usd < 35


def test_profit_zero_weight_uses_default():
    p = calculate_profit(source_price_cny=10, target_price_usd=20, weight_g=0)
    # Falls back to 250g tier
    assert p.fba_fee_usd == 3.86


def test_price_gap_ratio_basic():
    # 48 CNY → 6.72 USD; 32.99 / 6.72 ≈ 4.91
    assert round(price_gap_ratio(48.0, 32.99), 2) == 4.91


def test_price_gap_ratio_zero_source_returns_zero():
    assert price_gap_ratio(0.0, 99.99) == 0.0


def test_match_price_gap_filters_low_ratio():
    # Competitor at $1 (ratio < 1.5) must be dropped.
    src = [
        {
            "product_id": "1",
            "name_cn": "X",
            "name_en": "X",
            "price_cny": 100,
            "category": "c",
            "weight_g": 50,
        }
    ]
    competitors = [{"price_usd": 5.0, "category": "c", "review_count": 10}]  # ratio 0.36
    out = match_price_gap(src, competitors)
    assert out == []


def test_match_price_gap_high_ratio_kept():
    src = [
        {
            "product_id": "1",
            "name_cn": "X",
            "name_en": "X",
            "price_cny": 10,
            "category": "c",
            "weight_g": 50,
        }
    ]
    competitors = [{"price_usd": 50.0, "category": "c", "review_count": 100}]  # ratio ≈ 35.7
    out = match_price_gap(src, competitors)
    assert len(out) == 1
    assert out[0]["product_id"] == "1"
    assert out[0]["target_price_usd"] == 50.0
    assert out[0]["price_gap_ratio"] > 30


def test_match_price_gap_synthesizes_when_no_competitor():
    src = [
        {
            "product_id": "1",
            "name_cn": "X",
            "name_en": "X",
            "price_cny": 100,
            "category": "c",
            "weight_g": 50,
        }
    ]
    out = match_price_gap(src, [])
    # No competitor → synthesized target at 3x landed = 42 USD → ratio 3.0 (above 1.5)
    assert len(out) == 1
    assert out[0]["price_gap_ratio"] == 3.0