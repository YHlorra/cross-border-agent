"""SEAM-L3 tests: to_sp_api_payload / render_export / resolve_upload_channel.

The payload is the tier-1 (SP-API PUT) serialization AND the tier-2 export
file's core — one schema, two channels. Fixed fields come ONLY
from the market profile; variable values ONLY from ListingFacts; brand is
omitted when empty (无品牌豁免). No network, no LLM.
"""
from __future__ import annotations

from agent.listing.export import render_export
from agent.listing.schema import (
    ListingFacts,
    ListingOutput,
    MarketProfile,
    default_facts,
)
from agent.listing.serialize import to_sp_api_payload


def _profile() -> MarketProfile:
    return MarketProfile(
        marketplace_id="ATVPDKIKX0DER",
        language_tag="en_US",
        currency_code="USD",
        condition_default="new_new",
        fulfillment_channel_default="DEFAULT",
    )


def _output() -> ListingOutput:
    return ListingOutput(
        item_name="Smart Pet Feeder Automatic Dispenser",
        bullet_point=["B" * 30] * 5,
        product_description="Automatic feeder for cats and dogs.",
        generic_keyword="kibble camera monitor",
        product_type="PET_FEEDER",
    )


def _facts(**overrides: object) -> ListingFacts:
    facts: dict = {
        "price_usd": 89.99,
        "quantity": 1,
        "sku": "SKU-001",
        "brand": "Acme",
        "condition": "new_new",
        "fulfillment_channel": "DEFAULT",
    }
    facts.update(overrides)
    return ListingFacts(**facts)  # type: ignore[arg-type]


# --- payload ----------------------------------------------------------------

def test_payload_fixed_fields_come_from_profile() -> None:
    payload = to_sp_api_payload(_output(), _facts(), _profile())
    attrs = payload["attributes"]
    title_attr = attrs["item_name"][0]
    assert title_attr["marketplace_id"] == "ATVPDKIKX0DER"  # profile, not hardcoded
    assert title_attr["language_tag"] == "en_US"
    assert title_attr["value"] == "Smart Pet Feeder Automatic Dispenser"


def test_payload_variables_come_from_facts() -> None:
    payload = to_sp_api_payload(_output(), _facts(price_usd=19.5, quantity=7), _profile())
    attrs = payload["attributes"]
    assert attrs["purchasable_offer"][0]["our_price"][0]["schedule"][0][
        "value_with_currency"
    ] == "19.50USD"
    assert attrs["fulfillment_availability"][0]["quantity"] == 7
    assert attrs["condition_type"][0]["value"] == "new_new"


def test_payload_product_type_from_output() -> None:
    payload = to_sp_api_payload(_output(), _facts(), _profile())
    assert payload["product_type"] == "PET_FEEDER"


def test_payload_five_bullet_entries() -> None:
    payload = to_sp_api_payload(_output(), _facts(), _profile())
    assert len(payload["attributes"]["bullet_point"]) == 5


def test_payload_omits_brand_when_empty() -> None:
    payload = to_sp_api_payload(_output(), _facts(brand=""), _profile())
    assert "brand" not in payload["attributes"]


def test_payload_includes_brand_when_set() -> None:
    payload = to_sp_api_payload(_output(), _facts(brand="Acme"), _profile())
    assert payload["attributes"]["brand"][0]["value"] == "Acme"


def test_payload_skips_optional_context_fields() -> None:
    out = _output()
    out.subject_matter = "pet feeding"  # 辅助字段不上传
    payload = to_sp_api_payload(out, _facts(), _profile())
    assert "subject_matter" not in payload["attributes"]
    assert "target_audience" not in payload["attributes"]


# --- export rendering ---------------------------------------------------------

def _run(candidate: dict | None) -> dict:
    return {
        "id": "run-1",
        "product_id": "1688_004",
        "market_code": "US",
        "listing": {
            "listing": _output().model_dump(),
            "issues": [],
            "hard_failed": False,
        },
        "candidate": candidate,
    }


def _candidate_dict(**overrides: object) -> dict:
    """完整候选 dict — 与 save_listing_node 落库的 model_dump 同构。"""
    base: dict = {
        "product_id": "1688_004",
        "name_cn": "宠物自动喂食器",
        "name_en": "Smart Pet Feeder",
        "source_price_cny": 198.0,
        "target_price_usd": 89.99,
        "price_gap_ratio": 1.44,
        "category": "pet_supplies",
    }
    base.update(overrides)
    return base


def _run(candidate: dict | None) -> dict:
    return {
        "id": "run-1",
        "product_id": "1688_004",
        "market_code": "US",
        "listing": {
            "listing": _output().model_dump(),
            "issues": [],
            "hard_failed": False,
        },
        "candidate": candidate,
    }


def test_render_export_contains_payload_and_copy_blocks() -> None:
    export = render_export(_run(_candidate_dict()))
    assert "payload" in export and "copy_blocks" in export
    blocks = export["copy_blocks"]
    assert blocks["item_name"].startswith("Smart Pet Feeder")
    assert blocks["bullet_point"].count("\n") == 4  # 5 行
    assert export["copy_blocks"]["generic_keyword"] == "kibble camera monitor"
    assert export["payload"]["product_type"] == "PET_FEEDER"


def test_render_export_facts_default_from_candidate_price() -> None:
    export = render_export(_run(_candidate_dict(target_price_usd=42.0)))
    offer = export["payload"]["attributes"]["purchasable_offer"][0]
    assert offer["our_price"][0]["schedule"][0]["value_with_currency"] == "42.00USD"


def test_render_export_explicit_facts_win_over_candidate() -> None:
    export = render_export(
        _run(_candidate_dict(target_price_usd=42.0)),
        facts=ListingFacts(price_usd=55.0, quantity=1, sku="S", brand="B",
                           condition="new_new", fulfillment_channel="DEFAULT"),
    )
    offer = export["payload"]["attributes"]["purchasable_offer"][0]
    assert offer["our_price"][0]["schedule"][0]["value_with_currency"] == "55.00USD"


def test_render_export_without_candidate_needs_price() -> None:
    export = render_export(_run(None), facts=ListingFacts(price_usd=30.0, quantity=1,
                                                          sku="S", brand="",
                                                          condition="new_new",
                                                          fulfillment_channel="DEFAULT"))
    offer = export["payload"]["attributes"]["purchasable_offer"][0]
    assert offer["our_price"][0]["schedule"][0]["value_with_currency"] == "30.00USD"


# --- channel ----------------------------------------------------------------

def test_channel_api_when_creds_complete() -> None:
    from agent.listing.channel import resolve_upload_channel

    creds = {
        "client_id": "a", "client_secret": "b", "refresh_token": "c",
        "seller_id": "d", "marketplace_ids": ["ATVPDKIKX0DER"], "region": "NA",
    }
    assert resolve_upload_channel(creds) == "api"
    assert resolve_upload_channel(None) == "export"
    assert resolve_upload_channel({}) == "export"
    assert resolve_upload_channel({"client_id": "a"}) == "export"


def test_default_facts_condition_defaults_from_profile() -> None:
    facts = default_facts(profile=_profile())
    assert facts.condition == "new_new"
    assert facts.fulfillment_channel == "DEFAULT"


def test_render_export_product_type_override() -> None:
    # 人工确认卡放行/修改 product_type — 大写规范化进 payload, 不混入 facts
    export = render_export(
        _run(_candidate_dict()),
        facts_overrides={"product_type": "pet_feeder_v2", "sku": "S1"},
    )
    assert export["payload"]["product_type"] == "PET_FEEDER_V2"
    assert "product_type" not in export["facts"]
    assert export["facts"]["sku"] == "S1"
