"""Listing 模块：亚马逊官方格式合规的 Listing 生成与上传。

设计：API 优先 + 凭据检测退化 + 人工确认卡。
"""
from .channel import resolve_upload_channel
from .schema import (
    ListingFacts,
    ListingIssue,
    ListingOutput,
    MarketConfig,
    MarketFormat,
    MarketProfile,
    ValidationResult,
    default_facts,
    load_market_config,
)
from .serialize import to_sp_api_payload
from .validate import validate_listing

__all__ = [
    "ListingFacts",
    "ListingIssue",
    "ListingOutput",
    "MarketConfig",
    "MarketFormat",
    "MarketProfile",
    "ValidationResult",
    "default_facts",
    "load_market_config",
    "resolve_upload_channel",
    "to_sp_api_payload",
    "validate_listing",
]
