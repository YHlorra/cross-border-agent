"""export.py — 标准文件渲染（tier-2 复制粘贴通道）+ resolve_upload_channel。

导出文件 = to_sp_api_payload 的产物（tier-1 同一份 payload）+ 逐字段复制块。
可变商业参数来自 ListingFacts（CLI 参数 / 确认卡表单），固定字段来自市场
profile；价格缺省从落库的候选 target_price_usd 带出——变量 + 预填固定字段，
固定字段永不重复输入。
"""
from __future__ import annotations

from ..state import ProductCandidate
from .schema import (
    ListingFacts,
    ListingOutput,
    MarketConfig,
    default_facts,
    load_market_config,
)
from .serialize import to_sp_api_payload


def render_export(
    run: dict,
    facts: ListingFacts | None = None,
    market: MarketConfig | None = None,
    facts_overrides: dict | None = None,
) -> dict:
    """把落库的 listing run 渲染成标准导出文件。

    run: get_listing_run() 的返回（含 listing envelope 与可选 candidate）。
    facts 缺省时从候选带出价格、从 profile 取 condition/渠道，
    facts_overrides（CLI 参数 / 表单值）覆盖默认；
    旧记录无候选且无价格覆盖时价格缺省 0——调用方应显式传 --price。
    """
    market = market or load_market_config(code=run.get("market_code") or "US")
    envelope = run.get("listing") or {}
    output = ListingOutput.model_validate(envelope["listing"])

    overrides = dict(facts_overrides or {})
    pt = overrides.pop("product_type", None)  # 人工确认卡放行/修改
    if pt:
        output = output.model_copy(
            update={"product_type": str(pt).strip().upper()}
        )

    if facts is None:
        candidate_raw = run.get("candidate")
        candidate = (
            ProductCandidate.model_validate(candidate_raw)
            if isinstance(candidate_raw, dict)
            else None
        )
        facts = default_facts(
            candidate=candidate, profile=market.profile, **overrides
        )

    payload = to_sp_api_payload(output, facts, market.profile)
    copy_blocks = {
        "item_name": output.item_name,
        "bullet_point": "\n".join(
            f"{i}. {b}" for i, b in enumerate(output.bullet_point, 1)
        ),
        "product_description": output.product_description,
        "generic_keyword": output.generic_keyword,
    }
    return {
        "run_id": run.get("id"),
        "market_code": market.code,
        "payload": payload,
        "copy_blocks": copy_blocks,
        "facts": facts.model_dump(),
    }
