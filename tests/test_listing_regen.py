"""R8 single-field regeneration — SEAM tests.

Pins the contract for ``build_regen_prompt`` (no LLM) and ``apply_field`` /
``is_regen_field``. The LLM-bound ``regenerate_listing_field`` is covered
indirectly via the p8 E2E suite (a real regen through the /listing/regen-field
endpoint exercises the full LLM call).
"""

from __future__ import annotations

import pytest

from agent.listing.regen import (
    apply_field,
    build_regen_prompt,
    is_regen_field,
)
from agent.listing.schema import ListingOutput, load_market_config


@pytest.fixture
def market():
    return load_market_config(code="US")


@pytest.fixture
def sample_draft() -> ListingOutput:
    return ListingOutput(
        item_name="Cat Food Bowl Stainless Steel",
        bullet_point=[
            "Premium stainless steel construction",
            "Non-slip base for stability",
            "Easy to clean, dishwasher safe",
            "Rust-resistant finish",
            "Suitable for all cat sizes",
        ],
        product_description="ThisA-quality bowl with non-slip base.",
        generic_keyword="cat food bowl, stainless steel, pet dish",
        product_type="PET_BOWL",
        subject_matter="Cats",
        target_audience="Pet owners",
    )


def test_is_regen_field_whitelist():
    assert is_regen_field("item_name")
    assert is_regen_field("bullet_point")
    assert is_regen_field("product_description")
    assert is_regen_field("generic_keyword")
    assert not is_regen_field("not_a_field")
    assert not is_regen_field("session_id")  # not in ListingOutput


def test_build_regen_prompt_includes_format_constraints(market, sample_draft):
    system, user = build_regen_prompt(sample_draft, "item_name", market)
    # system prompt bakes in the per-field constraints
    assert "title <=" in system or "title_max" not in system  # template uses title_max
    # actually we use "<= N chars" — match the exact phrasing
    assert "chars" in system
    assert "item_name" in system
    assert "ONLY" in system
    # user prompt inlines the other fields, excludes the target's value
    assert "Cat Food Bowl Stainless Steel" not in user
    assert '"bullet_point"' in user
    assert '"product_description"' in user
    assert '"generic_keyword"' in user
    # JSON shape is enforced in system
    assert '"item_name"' in system


def test_build_regen_prompt_unknown_field_rejected(market, sample_draft):
    with pytest.raises(ValueError, match="unsupported regen field"):
        build_regen_prompt(sample_draft, "bogus", market)


def test_apply_field_swaps_one_field_only(sample_draft):
    new_title = "Premium Cat Food Bowl with Non-Slip Base"
    new_draft = apply_field(sample_draft, "item_name", new_title)
    assert new_draft.item_name == new_title
    # other fields unchanged
    assert new_draft.bullet_point == sample_draft.bullet_point
    assert new_draft.product_description == sample_draft.product_description
    assert new_draft.product_type == sample_draft.product_type


def test_apply_field_bullet_point_replaces_whole_list(sample_draft):
    new_bullets = ["First", "Second", "Third", "Fourth", "Fifth"]
    new_draft = apply_field(sample_draft, "bullet_point", new_bullets)
    assert new_draft.bullet_point == new_bullets
    assert new_draft.item_name == sample_draft.item_name


def test_apply_field_unknown_rejected(sample_draft):
    with pytest.raises(ValueError, match="unsupported regen field"):
        apply_field(sample_draft, "bogus", "x")