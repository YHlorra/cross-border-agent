"""绘蛙图片生成工具(供应商抽象,image_gen_provider 协议首实现)。

feature-wave-1 — `HUIWA_API_KEY` 缺失时工具不注册(agent 工具表不可见);
调用失败如实回填错误说明,不伪造 URL。
"""
from __future__ import annotations

import json
import os
from typing import Any

import aiohttp
from langchain_core.tools import BaseTool

HUIWA_ENDPOINT = "https://place.huiwa.cn/api/open/api/ai_image/generate"


class ImageGenProvider:
    """供应商抽象:generate(description, opts) → {"url": str}。"""

    name = "abstract"

    async def generate(self, description: str, opts: dict | None = None) -> dict:
        raise NotImplementedError


class HuiwaProvider(ImageGenProvider):
    """阿里绘蛙 open api 首实现。"""

    name = "huiwa"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    async def generate(self, description: str, opts: dict | None = None) -> dict:
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                HUIWA_ENDPOINT,
                headers={"Authorization": f"Bearer {self._key}"},
                json={"prompt": description, "count": 1},
            ) as resp:
                data = await resp.json(content_type=None)
        url = (data.get("data") or {}).get("url") or (data.get("data") or {}).get(
            "image_url"
        )
        if not url:
            raise RuntimeError(f"huiwa generate failed: {json.dumps(data)[:200]}")
        return {"url": url}


def get_image_provider() -> ImageGenProvider | None:
    key = os.environ.get("HUIWA_API_KEY")
    return HuiwaProvider(key) if key else None


def build_image_tools() -> list[Any]:
    """无键 → 空列表(工具对 agent 不可见);有键 → 绘蛙生成工具。"""
    provider = get_image_provider()
    if provider is None:
        return []

    class GenerateListingImage(BaseTool):
        name: str = "generate_listing_image"
        description: str = (
            "根据商品/卖点描述生成商品营销图(绘蛙)。输入为一段中文图片描述;"
            "返回含图片 URL 的 JSON。仅在有网且配置 HUIWA_API_KEY 时可用。"
        )

        def _run(self, description: str) -> str:
            raise NotImplementedError("sync path unused")

        async def _arun(self, description: str) -> str:
            try:
                out = await provider.generate(description)
                return json.dumps({"image_url": out["url"]}, ensure_ascii=False)
            except Exception as e:  # noqa: BLE001 — 工具错误如实回填
                return json.dumps({"error": str(e)}, ensure_ascii=False)

    return [GenerateListingImage()]
