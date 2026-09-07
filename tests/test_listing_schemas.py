"""SEAM-L0 tests: listing schemas — ListingOutput / ListingFacts / market config.

ListingOutput holds the LLM-generated content four-piece (field names mirror
SP-API `attributes`); ListingFacts holds the *variable* commercial parameters
(price / quantity / sku / brand) that the standard export file fills in —
variables, never hard-coded business values.
marketplaces.json is the single source for per-market format constraints and
the上架 profile with the fixed fields no one should re-type. No LLM involved.
"""
from __future__ import annotations

import json

import pytest

from agent.listing.schema import (
    ListingFacts,
    ListingOutput,
    MarketConfig,
    MarketProfile,
    default_facts,
    load_market_config,
)
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


def _profile() -> MarketProfile:
    return MarketProfile(
        marketplace_id="ATVPDKIKX0DER",
        language_tag="en_US",
        currency_code="USD",
        condition_default="new_new",
        fulfillment_channel_default="DEFAULT",
    )


def test_listing_output_minimal_defaults() -> None:
    out = ListingOutput(item_name="Smart Pet Feeder")
    assert out.bullet_point == []
    assert out.product_description == ""
    assert out.generic_keyword == ""
    assert out.product_type is None
    assert out.subject_matter is None
    assert out.target_audience is None


def test_listing_output_roundtrip_json() -> None:
    out = _sample_output()
    restored = ListingOutput.model_validate_json(out.model_dump_json())
    assert restored == out


def test_facts_price_default_carried_from_candidate() -> None:
    facts = default_facts(candidate=_candidate(), profile=_profile())
    assert facts.price_usd == 39.99  # 从选品候选 target_price_usd 带出
    assert facts.quantity == 1
    assert facts.sku == ""
    assert facts.brand == ""  # 空 → payload 省略 brand(无品牌豁免)


def test_facts_condition_and_channel_default_from_profile() -> None:
    facts = default_facts(profile=_profile())
    assert facts.condition == "new_new"
    assert facts.fulfillment_channel == "DEFAULT"


def test_facts_explicit_overrides_win() -> None:
    facts = default_facts(
        candidate=_candidate(), profile=_profile(),
        price_usd=19.99, quantity=500, sku="SKU-001", brand="Acme",
    )
    assert facts.price_usd == 19.99
    assert facts.quantity == 500
    assert facts.sku == "SKU-001"
    assert facts.brand == "Acme"


def test_load_market_config_real_file_us() -> None:
    cfg = load_market_config()  # 默认路径 = 仓库 data/selection/marketplaces.json
    assert cfg.code == "US"
    assert cfg.format.title_max_chars == 75  # 2026-07-27 新规口径
    assert cfg.format.bullet_count == 5
    assert cfg.format.search_terms_max_bytes == 250
    assert cfg.profile.marketplace_id == "ATVPDKIKX0DER"
    assert cfg.profile.language_tag == "en_US"


def test_load_market_config_unknown_code_raises() -> None:
    with pytest.raises(ValueError, match="unknown marketplace"):
        load_market_config(code="XX")


def test_load_market_config_corrupt_file_raises_not_silent(tmp_path) -> None:
    bad = tmp_path / "marketplaces.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_market_config(path=bad)


def test_load_market_config_missing_format_keys_raises(tmp_path) -> None:
    bad = tmp_path / "marketplaces.json"
    bad.write_text(json.dumps({"US": {"format": {}, "profile": {}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_market_config(path=bad)


def _sample_output() -> ListingOutput:
    return ListingOutput(
        item_name="Smart Pet Feeder Automatic Dog Cat Food Dispenser with Timer",
        bullet_point=["PROGRAMMABLE MEAL TIMES: 4 meals per day"] * 5,
        product_description="Automatic feeder for cats and dogs.",
        generic_keyword="kibble auto treat camera monitor",
        product_type="PET_FEEDER",
        subject_matter="pet feeding",
        target_audience="dog owners",
    )


def test_prompt_loader_listing_subdir() -> None:
    from agent.prompts import load_prompt

    meta, body = load_prompt("draft", subdir="listing")
    assert meta.get("name") == "draft"
    assert "item_name" in body  # 输出格式节与 schema 同步
