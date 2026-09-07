"""SEAM — loop runtime driver (stream_run_loop) contract with a scripted
model. No LLM/network: drives the same agent assembly the server uses and
asserts the emitted event stream + final envelope shapes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from agent.agents.loop_runtime import (
    _decision_from,
    _final_envelope,
    _parse_scored_candidates,
    stream_run_loop,
)
from agent.agents.tools import build_agent_tools


@tool
def probe_lookup(keyword: str) -> str:
    """Probe lookup tool."""
    return json.dumps(
        [
            {
                "product_id": "1688_001",
                "name_cn": "骨传导耳机",
                "name_en": "Bone Conduction Headphones",
                "source_price_cny": 35.0,
                "target_price_usd": 39.99,
                "price_gap_ratio": 5.9,
                "category": "electronics_audio",
                "review_count": 100,
                "total_score": 8.0,
                "recommendation": "go",
                "scores": [],
                "opportunities": ["低竞争细分"],
                "risks": [],
            }
        ],
        ensure_ascii=False,
    )


class Scripted(BaseChatModel):
    script: list[dict]
    turn: int = 0

    def __init__(self, script):
        super().__init__(script=script)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        step = self.script[self.turn]
        self.turn += 1
        if step["kind"] == "tool":
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": step["tool"],
                                    "args": step["args"],
                                    "id": f"c{self.turn}",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=step["text"]))]
        )

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self):
        return "fake"


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_parse_scored_candidates_from_markdown_table() -> None:
    md = """## 候选商品排序
| 商品 | 得分 | 推荐 |
|------|------|------|
| 骨传导耳机 | 7.2 | go |
| 无线耳机 | 6.1 | caution |
- 无线耳机 (6.1 分, caution)
"""
    parsed = _parse_scored_candidates(md)
    # table rows parse now (the dash line is the same candidate → deduped)
    assert [(p["name_cn"], p["total_score"], p["recommendation"]) for p in parsed] == [
        ("骨传导耳机", 7.2, "go"),
        ("无线耳机", 6.1, "caution"),
    ]


def test_parse_table_row_picks_total_before_rec_column() -> None:
    """五维得分一览 columns: the bare decimal nearest the rec column is the
    综合得分, not the first dimension score."""
    md = """| 商品 | 需求 | 竞争 | 利润 | 季节 | 复购 | 总分 | 推荐 |
|---|---|---|---|---|---|---|---|
| 骨传导耳机 | 8.0 | 7.5 | 6.9 | 7.1 | 6.5 | 7.2 | go |
"""
    parsed = _parse_scored_candidates(md)
    assert len(parsed) == 1
    assert parsed[0]["total_score"] == 7.2
    assert parsed[0]["recommendation"] == "go"


def test_parse_table_price_column_is_not_score() -> None:
    md = "| 商品 | 售价 | 得分 | 推荐 |\n|---|---|---|---|\n| 耳机 | 39.99 | 6.5 | no-go |"
    parsed = _parse_scored_candidates(md)
    assert parsed[0]["total_score"] == 6.5
    assert parsed[0]["recommendation"] == "no-go"


def test_parse_skips_non_scored_lines() -> None:
    md = "## 市场概览\n耳机市场整体增长。\n没有分数行。"
    assert _parse_scored_candidates(md) == []


def test_parse_plain_bullets_are_not_candidates() -> None:
    """plain dash bullets (行动建议 lines, notes) must not become
    fake candidates: only a dash line carrying both a score and a rec label
    qualifies, and `\bgo\b` keeps "category"/"logo" from reading as go."""
    md = "\n".join(
        [
            "- 首批备货 300 件",
            "- 供应商筛选标准：48 小时发货",
            "- 类目 logo 与 category 页面已核对",
            "- 无线耳机 (6.1 分, caution)",
        ]
    )
    parsed = _parse_scored_candidates(md)
    assert [(p["name_cn"], p["total_score"], p["recommendation"]) for p in parsed] == [
        ("无线耳机", 6.1, "caution"),
    ]


def test_decision_from_top_candidate() -> None:
    cands = [
        {"name_cn": "a", "total_score": 6.0, "recommendation": "caution"},
        {"name_cn": "b", "total_score": 8.0, "recommendation": "go"},
    ]
    assert _decision_from(cands, "") == "go"


def test_decision_no_candidates_no_go() -> None:
    assert _decision_from([], "没有找到合适商品") == "no-go"


def test_final_envelope_shape_matches_persist_contract() -> None:
    env = _final_envelope(
        agent_text="## 市场概览\n测试",
        candidates=[
            {
                "product_id": "p1",
                "name_cn": "x",
                "total_score": 8.0,
                "recommendation": "go",
                "opportunities": ["o1"],
                "risks": [],
            }
        ],
        session_id="s1",
        run_id="r1",
    )
    assert env["event"] == "final"
    assert env["decision"] == "go"
    assert env["session_id"] == "s1"
    assert env["run_id"] == "r1"
    assert env["candidates"][0]["product_id"] == "p1"
    assert env["report"]["top_recommendations"][0]["opportunities"] == ["o1"]


# ── driver (scripted agent, no network) ─────────────────────────────────────


async def _drive(script: list[dict], agents_models) -> list[dict]:
    model = Scripted(script)
    agents_models = dict(agents_models)
    # The loop runtime builds its own tools with the model for scoring; we
    # keep the model non-scoring here so the registry builds with the probe
    # tools appended by the test (score tool short-circuits without model).
    agents_models["selection"] = model
    events: list[dict] = []
    # Patch build_agent_tools to add the probe tool
    import agent.agents.loop_runtime as rt

    orig = rt.build_agent_tools

    def _with_probe(**kw):
        return [*orig(**kw), probe_lookup]

    rt.build_agent_tools = _with_probe
    try:
        async for ev in stream_run_loop(
            query="查耳机",
            session_id="s1",
            run_id="r1",
            history_context="",
            agents_models=agents_models,
        ):
            events.append(ev)
    finally:
        rt.build_agent_tools = orig
    return events


def test_loop_stream_emits_tool_and_final_events() -> None:
    script = [
        {"kind": "tool", "tool": "probe_lookup", "args": {"keyword": "耳机"}},
        {"kind": "final", "text": "## 市场概览\n耳机市场…\n## 候选商品排序\n- 骨传导耳机 (8.0 分, go)"},
    ]
    events = asyncio.run(_drive(script, {"selection": None}))
    kinds = [e.get("event") for e in events]
    assert "turn_start" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "final"
    final = events[-1]
    assert final["session_id"] == "s1"
    assert final["run_id"] == "r1"
    # markdown parse found the dash candidate
    assert final["candidates"] and final["candidates"][0]["total_score"] == 8.0


def test_loop_stream_final_decision_from_text_when_no_parse() -> None:
    script = [
        {"kind": "final", "text": "## 市场概览\n直接回答，无评分表"},
    ]
    events = asyncio.run(_drive(script, {"selection": None}))
    final = events[-1]
    assert final["event"] == "final"
    assert final["candidates"] == []


# ── terminal-state sources ( 2.7) ─────────────────────────────────────────


def test_loop_stream_final_takes_submit_report() -> None:
    """submit_report tool_call beats the score_candidates/markdown paths:
    the final envelope's candidates/decision/seed_keyword come from its
    validated args, and the wire event vocabulary is unchanged."""
    probe_row = {
        "product_id": "1688_001",
        "name_cn": "骨传导耳机",
        "name_en": "Bone Conduction Headphones",
        "source_price_cny": 35.0,
        "target_price_usd": 39.99,
        "price_gap_ratio": 5.9,
        "category": "electronics_audio",
        "review_count": 100,
        "scores": [],
        "total_score": 8.0,
        "recommendation": "go",
        "opportunities": ["低竞争细分"],
        "risks": [],
    }
    script = [
        {"kind": "tool", "tool": "probe_lookup", "args": {"keyword": "耳机"}},
        # no scoring model wired in this test → the score tool returns an
        # error observation; the run continues  and submit_report decides
        {"kind": "tool", "tool": "score_candidates", "args": {"candidates_json": "[]", "seed_keyword": ""}},
        {
            "kind": "tool",
            "tool": "submit_report",
            "args": {
                "candidates_json": json.dumps([probe_row], ensure_ascii=False),
                "decision": "go",
                "seed_keyword": "耳机",
            },
        },
        {"kind": "final", "text": "## 市场概览\n报告正文（含诱导性 go 字样）"},
    ]
    events = asyncio.run(_drive(script, {"selection": None}))
    kinds = [e.get("event") for e in events]
    assert "turn_start" in kinds and "tool_call" in kinds and "tool_result" in kinds
    assert kinds[-1] == "final"
    final = events[-1]
    assert final["decision"] == "go"
    # candidates come back as ScoredCandidate.model_dump — optional
    # ProductCandidate fields present with None defaults; wave1 起多 trend_series
    assert final["candidates"] == [
        {
            **probe_row,
            "bsr_rank": None,
            "bsr_top_percent": None,
            "monthly_search": None,
            "trend_growth": None,
            "trend_series": [],
            "weight_g": None,
            "image_url": None,
        }
    ]
    assert final["report"]["seed_keyword"] == "耳机"
    assert final["market_summary"] == "## 市场概览\n报告正文（含诱导性 go 字样）"


def test_loop_stream_no_go_text_not_reversed() -> None:
    """Fallback regression ( 2.4): an all-no-go report with no parseable
    candidates must yield decision no-go — the old "go" substring match
    reversed the direction."""
    script = [
        {
            "kind": "final",
            "text": "## 市场概览\n该品类当前不建议进入：全部候选 no-go。\n## 行动建议\n- 换方向",
        },
    ]
    events = asyncio.run(_drive(script, {"selection": None}))
    assert events[-1]["decision"] == "no-go"


# ── cancel path ( — client stops mid-run) ─────────────────────────────────


class HangingModel(BaseChatModel):
    """_generate blocks on a threading.Event — the executor-thread stand-in
    for an in-flight LLM call. (threading.Event, not asyncio.Event: the
    executor thread cannot await asyncio primitives.) Cancelling the asyncio
    task does NOT interrupt this thread; the sentinel exists only so the
    test can let it exit."""

    release: Any = None

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.release.wait(timeout=5)
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="late"))]
        )

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self):
        return "fake"


def test_loop_cancel_closes_and_logs(caplog) -> None:
    """Client stop = aclose mid-stream. The generator's finally must cancel
    the in-flight agent task and log it — the assertion targets the cancel
    LOG because that is the only observable proof the task was cancelled
    (a generator-side sentinel would only prove the generator ended)."""
    release = threading.Event()
    agents_models = {"selection": HangingModel(release=release)}

    async def _scenario() -> None:
        gen = stream_run_loop(
            query="查耳机",
            session_id="s1",
            run_id="r-cancel",
            history_context="",
            agents_models=agents_models,
        )
        first = await gen.__anext__()
        assert first.get("event") == "turn_start"
        # client disconnect → server closes the generator (aclosing in the
        # throat / GeneratorExit here)
        await gen.aclose()
        # release the executor thread while the loop is still alive so the
        # cancelled future settles cleanly before asyncio.run tears it down
        release.set()
        await asyncio.sleep(0.2)

    caplog.set_level(logging.INFO, logger="agent.agents.loop_runtime")
    t0 = time.monotonic()
    try:
        asyncio.run(_scenario())
    finally:
        release.set()
    assert time.monotonic() - t0 < 1.0
    assert "selection run r-cancel cancelled by client" in caplog.text
