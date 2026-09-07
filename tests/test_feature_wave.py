"""feature-wave-1 后端测试:PATCH fields 白名单 + 绘蛙工具条件注册。

无 HUIWA_API_KEY → 工具不注册;有键(mock HTTP)→ 生成并返回 URL。
PATCH 走真实 server handler(需要 app fixture,直接用 store + 路由函数单测)。
"""
from __future__ import annotations

import json

import pytest

from agent.agents.image_tool import build_image_tools
from agent.persistence import store


@pytest.fixture()
def listing_run(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    rid = store.save_listing_run(
        session_id="s-t",
        product_id="p-t",
        market_code="US",
        listing={"item_name": "旧名", "bullet_point": ["a", "b"]},
    )
    return rid


def test_image_tools_hidden_without_key(monkeypatch) -> None:
    from agent.agents.listing_agent import build_listing_tools

    monkeypatch.delenv("HUIWA_API_KEY", raising=False)
    tools = build_listing_tools()
    assert all(t.name != "generate_listing_image" for t in tools)


def test_image_tools_present_with_key(monkeypatch) -> None:
    monkeypatch.setenv("HUIWA_API_KEY", "test-key")
    from agent.agents.listing_agent import build_listing_tools
    from agent.agents.image_tool import build_image_tools

    tools = build_listing_tools()
    names = [t.name for t in tools]
    assert "generate_listing_image" in names


async def test_image_provider_generate(monkeypatch) -> None:
    """mock HTTP:绘蛙 provider 返回 URL(不走真实网络)。"""
    from agent.agents.image_tool import HuiwaProvider

    class _FakeResp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self, content_type=None):
            return {"data": {"url": "https://img.example/x.png"}}

    class _FakeSession:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, *a, **k):
            return _FakeResp()

    monkeypatch.setattr("aiohttp.ClientSession", _FakeSession)
    provider = HuiwaProvider("k")
    out = await provider.generate("宠物毛刷 白底图")
    assert out["url"] == "https://img.example/x.png"


def test_patch_fields_whitelist_and_write(listing_run) -> None:
    """白名单字段写入成功;越权字段抛 ValueError 语义由路由层 400 拦截。"""
    store.update_listing_field(listing_run, "item_name", "新名")
    got = store.get_listing_run(listing_run)
    assert got["listing"]["item_name"] == "新名"


def test_patch_fields_validation_shape() -> None:
    """ListingFieldsPatch 模型:extra=forbid + 白名单在 handler 层。"""
    from agent.server import ListingFieldsPatch
    from pydantic import ValidationError

    ok = ListingFieldsPatch.model_validate(
        {"fields": {"item_name": "x", "title_en": "y"}}
    )
    assert ok.fields["item_name"] == "x"
    with pytest.raises(ValidationError):
        ListingFieldsPatch.model_validate({"fields": {"evil": "x"}, "bogus": 1})


def test_candidate_trend_series_model_dump() -> None:
    """ProductCandidate.trend_series 默认空序列,赋值后随 model_dump 下发。"""
    from agent.state import ProductCandidate

    base = ProductCandidate(
        product_id="p1", name_cn="梳子", name_en="comb",
        source_price_cny=5.0, target_price_usd=9.9,
        price_gap_ratio=1.9, category="pet_supplies",
    )
    assert base.model_dump()["trend_series"] == []
    with_series = base.model_copy(update={"trend_series": [1.0, 2.0, 3.0]})
    assert with_series.model_dump()["trend_series"] == [1.0, 2.0, 3.0]


def test_match_candidates_returns_empty_trend_series_on_honest_empty() -> None:
    """selection-cleanup 2026-09-06: category_trends JSON fixture 已删,
    match_candidates 0 候选时返回 {"candidates": [], "note": "..."} 包络
    —— 真有候选时,每行 trend_series 必须是 list 类型(包装层兜底空序列)。"""
    from agent.agents.tools import build_agent_tools

    tools = build_agent_tools()
    match = next(t for t in tools if t.name == "match_candidates")
    payload = json.loads(match.invoke({"keyword": "耳机"}))
    # 0 候选包络
    assert isinstance(payload, dict)
    assert payload["candidates"] == []
    assert "note" in payload
