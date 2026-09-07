"""to_sp_api_payload — tier-1 serialization of a validated listing .

One schema, two channels: this exact payload is what the SP-API
Listings Items PUT uploads AND what the export file renders. Fixed fields
(marketplace_id / language_tag / currency) come ONLY from the market
profile; variable values (price / quantity / sku / brand / condition /
fulfillment) ONLY from ListingFacts — zero hard-coded business values.
"""
from __future__ import annotations

from .schema import ListingFacts, ListingOutput, MarketProfile


def _content_entry(value: str, profile: MarketProfile) -> dict:
    return {
        "value": value,
        "language_tag": profile.language_tag,
        "marketplace_id": profile.marketplace_id,
    }


def to_sp_api_payload(
    output: ListingOutput,
    facts: ListingFacts,
    profile: MarketProfile,
) -> dict:
    """Serialize a validated draft + seller facts into the SP-API
    Listings Items PUT body (PUT /listings/2021-08-01/.../items/{sku})."""
    attrs: dict = {
        "item_name": [_content_entry(output.item_name, profile)],
        "bullet_point": [
            _content_entry(b, profile) for b in output.bullet_point
        ],
        "product_description": [
            _content_entry(output.product_description, profile)
        ],
        "generic_keyword": [{"value": output.generic_keyword}],
        "condition_type": [{"value": facts.condition}],
        "fulfillment_availability": [
            {
                "fulfillment_channel_code": facts.fulfillment_channel,
                "quantity": facts.quantity,
            }
        ],
        "purchasable_offer": [
            {
                "marketplace_id": profile.marketplace_id,
                "currency": profile.currency_code,
                "our_price": [
                    {
                        "schedule": [
                            {
                                "value_with_currency": (
                                    f"{facts.price_usd:.2f}{profile.currency_code}"
                                )
                            }
                        ]
                    }
                ],
            }
        ],
    }
    if output.product_type:
        payload_product_type = output.product_type
    else:
        payload_product_type = "PRODUCT"  # SP-API 通用兜底类型
    if facts.brand:
        attrs["brand"] = [{"value": facts.brand}]
    return {"product_type": payload_product_type, "attributes": attrs}
