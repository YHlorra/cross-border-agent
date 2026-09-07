"""SEAM — agent tools registry behavior with an in-memory adapter.

No LLM: deterministic tools (match/hot_filter/trend/profit) exercise the
LocalJSONAdapter fixtures; score_candidates short-circuits when no model is
wired. Mirrors the test_adapter_search.py fixture idiom.
"""
from __future__ import annotations

import json

import pytest

from agent.agents.tools import MAX_SCORE_PER_CALL, build_agent_tools


@pytest.fixture(scope="module")
def tools():
    return build_agent_tools()


def _invoke(tool, **kwargs):
    raw = tool.invoke(kwargs)
    return json.loads(raw)


def _scored(rec="go", score=7.2):
    """A minimal ScoredCandidate-valid payload ( submit_report tests)."""
    return {
        "product_id": "1688_001",
        "name_cn": "骨传导耳机",
        "name_en": "Bone Conduction Headphones",
        "source_price_cny": 35.0,
        "target_price_usd": 39.99,
        "price_gap_ratio": 5.9,
        "category": "electronics_audio",
        "review_count": 100,
        "scores": [],
        "total_score": score,
        "recommendation": rec,
        "opportunities": [],
        "risks": [],
    }


def _submit_tool(tools):
    return next(t for t in tools if t.name == "submit_report")


def test_registry_has_ten_tools(tools) -> None:
    names = [t.name for t in tools]
    assert names == [
        "search_1688",
        "search_amazon",
        "match_candidates",
        "hot_filter_candidates",
        "category_trend",
        "calculate_profit_tool",
        "score_candidates",
        "submit_report",
        "graph_traverse",
        "corpus_overview",
    ]


def test_graph_traverse_reads_memory_graph(tmp_path) -> None:
    """graph_traverse is registered in the production tool registry
    and reads the entity_edges table scoped by the injected db_path."""
    from agent.agents.memory_graph import write_edge

    db = tmp_path / "mg.db"
    write_edge(
        src_type="run",
        src_id="r1",
        rel="decided",
        dst_type="product",
        dst_id="1688_004",
        run_id="r1",
        db_path=db,
    )
    gt = [t for t in build_agent_tools(db_path=db) if t.name == "graph_traverse"][0]
    out = gt.invoke({"entity_type": "run", "entity_id": "r1", "hops": 1})
    assert "decided" in out and "1688_004" in out

    # unknown rel matches nothing (empty result is an honest answer, not an error)
    assert gt.invoke({"entity_type": "run", "entity_id": "r1", "rel": "nope"}) == "(无相关记忆)"
    # hops bound (>2) rejected as a JSON error payload, never an exception
    bad = json.loads(gt.invoke({"entity_type": "run", "entity_id": "r1", "hops": 3}))
    assert "error" in bad


def test_match_candidates_returns_honest_empty_when_no_1688_source(tools) -> None:
    """selection-cleanup 2026-09-06: 1688 货源 JSON fixture 已删,
    match_candidates 的 1688 侧从 PG 语料合成（PgCorpusAdapter.search_1688
    继承 LocalJSONAdapter 现在是 no-op）——0 货源意味着报告需要如实说
    「缺 1688 货源真源」,不构造候选行。这是契约 pin，不是 bug。"""
    rows = _invoke(tools[2], keyword="耳机")
    # match_candidates 0 候选时返回 {"candidates": [], "note": "..."} 包络
    assert isinstance(rows, dict) and rows["candidates"] == [], (
        "selection-cleanup 后 1688 侧无源,match_candidates 应返回空 candidates 包络"
    )
    assert "note" in rows and "未匹配" in rows["note"]


def test_hot_filter_pure_rules_no_model() -> None:
    """selection-cleanup 2026-09-06:hot_filter 规则与模型无关,可纯函数测。
    LocalJSONAdapter 现在返回 [] —— 此处直接构造候选行验证 hot_filter
    本身 (passed/reasons 形状 + 通过条件),不依赖 adapter 数据。"""
    from agent.tools.adapters.local_json import LocalJSONAdapter

    local_tools = build_agent_tools(adapter=LocalJSONAdapter())
    cand = [
        {
            "product_id": "test_001",
            "name_cn": "测试商品",
            "name_en": "Test Product",
            "source_price_cny": 50.0,
            "target_price_usd": 99.99,
            "price_gap_ratio": 14.28,
            "category": "electronics_audio",
            "review_count": 500,
            "scores": [],
            "total_score": 8.0,
            "recommendation": "go",
            "opportunities": [],
            "risks": [],
        }
    ]
    out = _invoke(local_tools[3], candidates_json=json.dumps(cand, ensure_ascii=False))
    assert isinstance(out["passed"], list)
    assert isinstance(out["reasons"], dict)
    assert "test_001" in out["reasons"]
    assert "通过" in out["reasons"]["test_001"]


def test_category_trend_honest_empty(tools) -> None:
    """selection-cleanup 2026-09-06: JSON fixture 删了,category_trend 返回
    None —— tools 包装层要把 None 序列化成空字符串,UI 上是「无数据」标签。"""
    assert tools[4].invoke({"category": "pet_supplies"}) == ""
    assert tools[4].invoke({"category": "no_such_cat"}) == ""


def test_calculate_profit_deterministic(tools) -> None:
    b = _invoke(tools[5], source_price_cny=35.0, target_price_usd=39.99, weight_g=120)
    assert b["net_profit_usd"] > 0
    assert b["profit_margin"] > 0
    # fixed arithmetic — regression pin
    assert b["commission_usd"] == pytest.approx(39.99 * 0.15, abs=1e-9)


def test_score_candidates_without_model_returns_wire_error(tools) -> None:
    cand = _invoke(tools[2], keyword="耳机")
    raw = tools[6].invoke({"candidates_json": json.dumps(cand, ensure_ascii=False), "seed_keyword": "耳机"})
    out = json.loads(raw)
    assert out.get("error") and "not wired" in out["error"]


def test_score_candidates_caps_batch_size(tools) -> None:
    assert MAX_SCORE_PER_CALL == 3


# ── submit_report — the structured terminal exit ( 2.7) ───────────────────


def test_submit_report_accepts_valid_payload(tools) -> None:
    out = _invoke(
        _submit_tool(tools),
        candidates_json=json.dumps([_scored()], ensure_ascii=False),
        decision="go",
        seed_keyword="耳机",
    )
    assert out == {"ok": True, "count": 1, "decision": "go", "seed_keyword": "耳机"}


def test_submit_report_rejects_decision_top_mismatch(tools) -> None:
    """decision must equal the top-scored candidate's recommendation — an
    all-no-go batch submitted as go is an error observation (D9), not silent
    acceptance."""
    out = _invoke(
        _submit_tool(tools),
        candidates_json=json.dumps([_scored(rec="no-go", score=7.2)], ensure_ascii=False),
        decision="go",
        seed_keyword="耳机",
    )
    assert "error" in out
    assert "recommendation" in out["error"]


def test_submit_report_rejects_invalid_json(tools) -> None:
    out = json.loads(_submit_tool(tools).invoke({"candidates_json": "{not json", "decision": "go"}))
    assert "error" in out and "invalid candidates_json" in out["error"]


def test_search_1688_honest_empty(tools) -> None:
    """selection-cleanup 2026-09-06: 1688 货源 JSON fixture 已删,search_1688
    现在返回 [] —— 这是契约 pin,不是 bug。"""
    rows = _invoke(tools[0], keyword="宠物用品", max_price_cny=200)
    assert rows == []


def test_search_amazon_hits_pg_corpus(tools) -> None:
    """默认 registry 亚马逊侧走 PG 语料（PgCorpusAdapter.search_amazon）——
    SEAM 见 test_pg_corpus_adapter。本测试 pin 默认 registry 这一选择:
    即便不显式传 adapter,search_amazon 也必须有 PG 命中。**keyword 用
    英文**（selection-skill 语言契约:语料是英文数据,适配器不翻译）。"""
    rows = _invoke(tools[1], keyword="headphones")
    assert rows, "默认 registry 亚马逊侧应命中 PG 语料"
    assert "asin" in rows[0]
    assert rows[0]["asin"].startswith("B0")
