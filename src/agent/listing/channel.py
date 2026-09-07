"""resolve_upload_channel — 凭据检测的通道分流（纯函数，SEAM）。

api = SP-API 凭据齐全，自动上传（目标路径）；
export = 无凭据 → 导出标准文件供复制粘贴（退化路径）。
退化是通道切换不是质量降级：两通道共享同一份校验后的 payload。
"""
from __future__ import annotations

_REQUIRED_SPAPI_KEYS = (
    "client_id",
    "client_secret",
    "refresh_token",
    "seller_id",
    "marketplace_ids",
    "region",
)


def resolve_upload_channel(spapi_creds: dict | None) -> str:
    """凭据齐全 → "api"；缺失/损坏 → "export"（不抛错，缺什么走什么通道）。"""
    if not isinstance(spapi_creds, dict):
        return "export"
    return (
        "api"
        if all(spapi_creds.get(k) for k in _REQUIRED_SPAPI_KEYS)
        else "export"
    )
