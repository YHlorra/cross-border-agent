"""SP-API client — LWA token refresh + Listings Items PUT.

无 mock：无凭据环境根本不会实例化（channel 分流先行）；
上传失败时把上游 status/body 原样返回给调用方透传（R3 红线），不包装。
gated live 验证见 tests/test_listing_graph_live.py（SPAPI_* 环境变量门控）。
"""
from __future__ import annotations

import aiohttp

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

_REGION_ENDPOINTS = {
    "NA": "https://sellingpartnerapi-na.amazon.com",
    "EU": "https://sellingpartnerapi-eu.amazon.com",
    "FE": "https://sellingpartnerapi-fe.amazon.com",
}

_REQUIRED = ("client_id", "client_secret", "refresh_token", "seller_id", "marketplace_ids", "region")


class SpapiClient:
    """Thin async client: one token refresh per call batch, one PUT per listing."""

    def __init__(self, creds: dict) -> None:
        missing = [k for k in _REQUIRED if not creds.get(k)]
        if missing:
            raise ValueError(f"SP-API credentials missing: {missing}")
        self._creds = creds
        endpoint = _REGION_ENDPOINTS.get(str(creds["region"]).upper())
        if endpoint is None:
            raise ValueError(f"unknown SP-API region: {creds['region']!r}")
        self._endpoint = endpoint

    async def _access_token(self) -> str:
        c = self._creds
        async with aiohttp.ClientSession() as session:
            async with session.post(
                LWA_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": c["client_id"],
                    "client_secret": c["client_secret"],
                    "refresh_token": c["refresh_token"],
                },
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200 or "access_token" not in body:
                    raise RuntimeError(f"LWA token refresh failed: {resp.status} {body}")
                return body["access_token"]

    async def put_listing(self, sku: str, payload: dict) -> dict:
        """PUT /listings/2021-08-01/marketplaces/{ids}/items/{sellerId}/{sku}.

        返回 {"status": int, "body": 上游 JSON 原文}——上游错误不包装不重试。
        """
        token = await self._access_token()
        url = (
            f"{self._endpoint}/listings/2021-08-01/marketplaces/"
            f"{','.join(self._creds['marketplace_ids'])}/items/"
            f"{self._creds['seller_id']}/{sku}"
        )
        headers = {
            "x-amz-access-token": token,
            "content-type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.put(url, headers=headers, json=payload) as resp:
                body = await resp.json(content_type=None)
                return {"status": resp.status, "body": body}
