"""Listing domain models — content (LLM), facts (variables), market config.

ListingOutput 的字段名与 Amazon SP-API ``attributes`` 键一一映射
(item_name / bullet_point / product_description / generic_keyword)，所以
tier-1（API 直填）是对同一份数据的序列化，tier-2（复制粘贴）是它的渲染，
两层永不漂移。

ListingFacts 是**可变商业参数**的变量容器（价格/数量/SKU/品牌…）——
标准文件模板里的每一个可填项，无一处硬编码业务数值；固定字段
（marketplace_id / condition / language_tag 等）不在这里，在市场 profile。

约束数值全部来自 marketplaces.json（静态规范数据，随代码入库），不进代码
——标题长度新规（2026-07-27）这类变化只改配置。
字段名将镜像到 ``lib/listing-types.ts``（S3 前端切片）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from ..state import ProductCandidate


class ListingOutput(BaseModel):
    """LLM 生成的内容四件套，合规校验层的作用对象。"""

    item_name: str
    bullet_point: list[str] = Field(default_factory=list)
    product_description: str = ""
    generic_keyword: str = ""
    product_type: Optional[str] = None  # LLM 提议，人工确认卡放行
    subject_matter: Optional[str] = None  # 辅助上下文，不进 payload
    target_audience: Optional[str] = None


class ListingIssue(BaseModel):
    """一条校验发现。severity: "auto_fixed"(已自动修复) | "hard_fail"(需重生成/人工处理)。"""

    field: str
    code: str
    message: str
    severity: str


class ValidationResult(BaseModel):
    """validate_listing 的产出：修复后的新对象 + 问题清单 + 硬失败标志。"""

    output: ListingOutput
    issues: list[ListingIssue] = Field(default_factory=list)
    hard_failed: bool = False


class ListingFacts(BaseModel):
    """可变商业参数——标准文件模板的可填变量。

    字段默认值只是容器缺省；真实默认由 :func:`default_facts` 从选品候选
    （价格带出）与市场 profile（condition / fulfillment）填充。
    """

    price_usd: float = 0.0
    quantity: int = 1
    sku: str = ""
    brand: str = ""  # 空 → payload 省略 brand 属性（Amazon 无品牌豁免路径）
    condition: str = ""
    fulfillment_channel: str = ""


class MarketFormat(BaseModel):
    """单个市场的格式硬约束（校验层消费）。"""

    title_max_chars: int
    bullet_count: int
    bullet_min_chars: int
    bullet_max_chars: int
    description_max_chars: int
    search_terms_max_bytes: int
    title_forbidden_chars: list[str] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)
    subjective_terms: list[str] = Field(default_factory=list)


class MarketProfile(BaseModel):
    """单个市场的上架 profile——固定字段唯一来源（模板渲染消费，永不重复输入）。"""

    marketplace_id: str
    language_tag: str
    currency_code: str
    condition_default: str
    fulfillment_channel_default: str


class MarketConfig(BaseModel):
    code: str
    format: MarketFormat
    profile: MarketProfile


# marketplaces.json 是随代码发布的静态规范（入库），不走 AGENT_DATA_DIR——
# 隔离数据目录的测试环境也必须能读到规范。
_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "selection"


def load_market_config(path: Path | None = None, code: str = "US") -> MarketConfig:
    """加载市场配置。文件损坏 / 未知市场码 / 缺段一律抛 ValueError，不静默降级。"""
    p = path or (_DATA_DIR / "marketplaces.json")
    raw = json.loads(p.read_text(encoding="utf-8"))
    entry = raw.get(code) if isinstance(raw, dict) else None
    if not isinstance(entry, dict):
        raise ValueError(f"unknown marketplace code: {code!r} in {p}")
    if "format" not in entry or "profile" not in entry:
        raise ValueError(f"marketplace {code!r} must contain 'format' and 'profile' in {p}")
    return MarketConfig(code=code, format=entry["format"], profile=entry["profile"])


def default_facts(
    candidate: ProductCandidate | None = None,
    profile: MarketProfile | None = None,
    **overrides: object,
) -> ListingFacts:
    """可变参数的智能默认：价格从选品候选带出，condition / 渠道从市场 profile 取。

    调用方（确认卡表单 / CLI --price 等）传入的 overrides 一律覆盖默认。
    """
    facts: dict = {
        "price_usd": candidate.target_price_usd if candidate else 0.0,
        "quantity": 1,
        "sku": "",
        "brand": "",
    }
    if profile is not None:
        facts["condition"] = profile.condition_default
        facts["fulfillment_channel"] = profile.fulfillment_channel_default
    facts.update(overrides)
    return ListingFacts(**facts)  # type: ignore[arg-type]
