"""Gated live test: real SP-API upload path ( tier-1).

Skipped unless all SPAPI_* env vars are set — the red line is "no mock":
without real credentials this path simply does not run, it never fakes.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live

_REQUIRED = (
    "SPAPI_CLIENT_ID",
    "SPAPI_CLIENT_SECRET",
    "SPAPI_REFRESH_TOKEN",
    "SPAPI_SELLER_ID",
)


def _require_env() -> dict:
    missing = [k for k in _REQUIRED if not os.environ.get(k)]
    if missing:
        pytest.skip(f"live test skipped — missing env: {missing}")
    return {
        "client_id": os.environ["SPAPI_CLIENT_ID"],
        "client_secret": os.environ["SPAPI_CLIENT_SECRET"],
        "refresh_token": os.environ["SPAPI_REFRESH_TOKEN"],
        "seller_id": os.environ["SPAPI_SELLER_ID"],
        "marketplace_ids": [os.environ.get("SPAPI_MARKETPLACE_IDS", "ATVPDKIKX0DER")],
        "region": os.environ.get("SPAPI_REGION", "NA"),
    }


def _sample_payload() -> dict:
    from agent.listing.schema import (
        ListingFacts,
        ListingOutput,
        MarketProfile,
    )
    from agent.listing.serialize import to_sp_api_payload

    profile = MarketProfile(
        marketplace_id="ATVPDKIKX0DER",
        language_tag="en_US",
        currency_code="USD",
        condition_default="new_new",
        fulfillment_channel_default="DEFAULT",
    )
    output = ListingOutput(
        item_name="Smart Pet Feeder Automatic Dispenser LIVE",
        bullet_point=["PROGRAMMABLE MEALS: four per day"] * 5,
        product_description="Live-test listing; safe to delete after run.",
        generic_keyword="p8 live test",
        product_type="PET_FEEDER",
    )
    facts = ListingFacts(
        price_usd=9.99,
        quantity=1,
        sku=os.environ.get("SPAPI_TEST_SKU", "LIVE-TEST"),
        brand="",
        condition=profile.condition_default,
        fulfillment_channel=profile.fulfillment_channel_default,
    )
    return to_sp_api_payload(output, facts, profile)


async def test_real_spapi_put_listing() -> None:
    creds = _require_env()
    from agent.listing.spapi import SpapiClient

    client = SpapiClient(creds)
    result = await client.put_listing(
        os.environ.get("SPAPI_TEST_SKU", "LIVE-TEST"),
        _sample_payload(),
    )
    # 上游结果原样断言：2xx 即成功；非 2xx 把 body 带进失败信息（不包装）
    assert 200 <= result["status"] < 300, f"upstream {result['status']}: {result['body']}"
