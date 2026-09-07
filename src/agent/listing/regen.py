"""Single-field regeneration — re-generate one ListingOutput field via LLM,
keep the other fields intact, re-run the SEAM validation layer.

The draft node is monolithic (4 LLM-driven fields in one pass). When a seller
dislikes one bullet point or the title, re-running the whole draft is wasteful
and risks regressing the other fields. This module is the surgical alternative.

Public surface:
- ``build_regen_prompt(current_draft, field_name, market)``: pure function,
  the prompt the LLM sees. SEAM-testable without a model.
- ``regenerate_listing_field(model, draft, field_name, market, ...)``: calls
  the LLM with a typed schema, swaps the new value into a copy of the draft,
  re-validates, returns the new draft + issues + hard_failed flag.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from ..llm import AimuxChatModel
from .schema import (
    ListingOutput,
    ListingIssue,
    MarketConfig,
    ValidationResult,
    load_market_config,
)
from .validate import validate_listing

# ListingOutput fields eligible for single-field regen. Keep in sync with
# schema.ListingOutput — adding a new content field requires adding it here
# and to FieldRegen below.
_REGEN_FIELDS: frozenset[str] = frozenset(
    {
        "item_name",
        "bullet_point",
        "product_description",
        "generic_keyword",
        "product_type",
        "subject_matter",
        "target_audience",
    }
)


class FieldRegen(BaseModel):
    """LLM returns the regenerated field under its canonical name.

    All fields are Optional so the schema stays compatible with the LLM
    occasionally returning extra context — only the requested field is read.
    """

    item_name: str | None = None
    bullet_point: list[str] | None = None
    product_description: str | None = None
    generic_keyword: str | None = None
    product_type: str | None = None
    subject_matter: str | None = None
    target_audience: str | None = None


def is_regen_field(name: str) -> bool:
    return name in _REGEN_FIELDS


def build_regen_prompt(
    current_draft: ListingOutput,
    field_name: str,
    market: MarketConfig,
) -> tuple[str, str]:
    """Build the (system, user) prompt for regenerating one field.

    Pure function — no LLM, no IO. The user prompt inlines the OTHER fields
    as anchors so the regenerated value stays semantically aligned with the
    rest of the listing. The system prompt bakes in the per-field format
    constraints from the market profile (title 75-char rule etc.).
    """
    if field_name not in _REGEN_FIELDS:
        raise ValueError(f"unsupported regen field: {field_name}")
    fmt = market.format
    system = (
        "You are an Amazon Listing optimizer. Your task is to regenerate ONLY "
        f"the '{field_name}' field of an existing listing.\n\n"
        "Constraints:\n"
        f"- Return ONLY the new value of '{field_name}'.\n"
        "- Keep semantic alignment with the other existing fields (they are anchors).\n"
        f"- Follow Amazon {market.code} format: "
        f"title <= {fmt.title_max_chars} chars, "
        f"bullets {fmt.bullet_count} items each {fmt.bullet_min_chars}-{fmt.bullet_max_chars} chars, "
        f"description <= {fmt.description_max_chars} chars, "
        f"search terms <= {fmt.search_terms_max_bytes} bytes.\n"
        f"- Return JSON: {{\"{field_name}\": <new value>}}"
    )
    context = current_draft.model_dump(exclude={field_name})
    user = (
        f"Current listing (excluding '{field_name}' which you are regenerating):\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        f"Regenerate field: {field_name}\n"
        f"Return ONLY this field's new value, JSON format: "
        f'{{"{field_name}": <new value>}}'
    )
    return system, user


def apply_field(
    draft: ListingOutput, field_name: str, new_value: Any
) -> ListingOutput:
    """Swap one field on a copy of the draft (typed-safe)."""
    if field_name not in _REGEN_FIELDS:
        raise ValueError(f"unsupported regen field: {field_name}")
    return draft.model_copy(update={field_name: new_value})


def _extract_field(out: FieldRegen, field_name: str) -> Any:
    val = getattr(out, field_name, None)
    if val is None:
        raise ValueError(f"LLM did not return the requested field: {field_name}")
    return val


async def regenerate_listing_field(
    model: AimuxChatModel,
    current_draft: ListingOutput,
    field_name: str,
    market: MarketConfig,
    competitor_brands: list[str] | None = None,
    *,
    temperature: float = 0.4,
) -> tuple[ListingOutput, list[ListingIssue], bool]:
    """Regenerate ``field_name``, re-validate the result, return new draft.

    Returns ``(new_draft, issues, hard_failed)``. The hard_failed flag is
    inherited from validate_listing — content-violations (promotional
    language, subjective claims) are surfaced honestly, not auto-fixed.
    """
    if field_name not in _REGEN_FIELDS:
        raise ValueError(f"unsupported regen field: {field_name}")
    system, user = build_regen_prompt(current_draft, field_name, market)
    out = await model.structured(
        system=system,
        user=user,
        schema=FieldRegen,
        temperature=temperature,
    )
    new_value = _extract_field(out, field_name)
    new_draft = apply_field(current_draft, field_name, new_value)
    result: ValidationResult = validate_listing(
        new_draft, market, competitor_brands
    )
    return result.output, result.issues, result.hard_failed