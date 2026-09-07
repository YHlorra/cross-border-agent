"""SEAM — selection agent loop contract with a scripted FakeChatModel.

Three scenarios (tasks.md 1.9): normal finish, budget-cap forced finish,
tool-failure refeed then finish. Asserts the wire event sequence and the
final envelope shape match the throat's save_run persistence contract.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from agent.agents.events_middleware import EventsMiddleware, MAX_TOOL_CALLS
from agent.agents.selection_agent import build_selection_agent
from agent.agents.tools import build_agent_tools


@tool
def probe_lookup(keyword: str) -> str:
    """Probe tool: returns a canned candidate JSON."""
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
                "weight_g": 120,
            }
        ],
        ensure_ascii=False,
    )


@tool
def probe_boom() -> str:
    """Probe tool that always fails."""

    def _boom() -> str:
        raise RuntimeError("fixture boom")

    return _boom()


class ScriptedChatModel(BaseChatModel):
    """Scripted responses: each element of ``script`` is a turn dict:

    {"kind": "tool", "tool": name, "args": {...}} → an AIMessage with a tool call
    {"kind": "final", "text": str}                → a text AIMessage
    {"kind": "raise"}                             → raises RuntimeError
    """

    script: list[dict]
    turn: int = 0

    def __init__(self, script: list[dict]):
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
                                    "id": f"call_{self.turn}",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )
        if step["kind"] == "raise":
            raise RuntimeError("terminal model failure")
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=step["text"]))]
        )

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self):
        return "fake"


async def _run_agent(script: list[dict], events: list[dict]) -> list[AIMessage]:
    """Drive one agent run; return the final state messages."""
    model = ScriptedChatModel(script)
    tools = [*build_agent_tools(), probe_lookup, probe_boom]
    agent = build_selection_agent(
        model=model,
        tools=tools,
        events_queue=None,  # middleware emit sink used instead
        history_context="",
        middleware=[EventsMiddleware(lambda ev: _append(events, ev))],
    )
    out = await agent.ainvoke({"messages": [HumanMessage(content="查耳机")]})
    return out["messages"]


def _append(events: list[dict], ev: dict) -> asyncio.coroutine:
    events.append(ev)
    return _noop()


async def _noop():
    return None


def _wire_events(events: list[dict]) -> list[str]:
    return [e.get("event") for e in events]


def test_normal_finish_emits_turn_and_tool_events() -> None:
    events: list[dict] = []
    msgs = asyncio.run(
        _run_agent(
            [
                {"kind": "tool", "tool": "probe_lookup", "args": {"keyword": "耳机"}},
                {"kind": "final", "text": "## 市场概览\n耳机市场…"},
            ],
            events,
        )
    )
    kinds = _wire_events(events)
    assert kinds.count("turn_start") == 2
    assert kinds.count("turn_end") == 2
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    # agent_delta carries the final answer (non-tool turn)
    deltas = [e for e in events if e["event"] == "agent_delta"]
    assert deltas and "耳机市场" in deltas[-1]["delta"]
    # final assistant message present in the graph state
    assert msgs[-1].content.startswith("## 市场概览")


def test_budget_cap_forces_finish_with_data() -> None:
    """Tool budget exhausted: the tool call is blocked with an honest error
    ToolMessage; the model (scripted) then emits a final answer. The budget
    counter is preset in abefore_agent (the hook that resets per run)."""
    events: list[dict] = []

    async def _run_with_exhausted_budget() -> list:
        model = ScriptedChatModel(
            [
                {"kind": "tool", "tool": "probe_lookup", "args": {"keyword": "耳机"}},
                {"kind": "final", "text": "## 报告\n基于现有数据收尾"},
            ]
        )
        tools = [*build_agent_tools(), probe_lookup, probe_boom]

        class _PresetMW(EventsMiddleware):
            """Applies the exhausted-budget preset inside abefore_agent so the
            per-run reset cannot clobber it."""

            def __init__(self, emit):
                self._preset = None
                super().__init__(emit)

            def preset(self, n):
                self._preset = n

            async def abefore_agent(self, state, runtime):
                await super().abefore_agent(state, runtime)
                if self._preset is not None:
                    self._tool_calls = self._preset
                    self._preset = None
                return None

        mw = _PresetMW(lambda ev: _append(events, ev))
        mw.preset(MAX_TOOL_CALLS + 1)  # exhausted before the run starts
        agent = build_selection_agent(model=model, tools=tools, middleware=[mw])
        out = await agent.ainvoke({"messages": [HumanMessage(content="查耳机")]})
        return out["messages"]

    msgs = asyncio.run(_run_with_exhausted_budget())
    # the blocked call surfaced as an error tool_result (budget_exhausted)
    blocked = [
        e
        for e in events
        if e["event"] == "tool_result" and e.get("error") == "budget_exhausted"
    ]
    assert len(blocked) == 1
    # the model saw the error ToolMessage in its history
    last_tm = [m for m in msgs if type(m).__name__ == "ToolMessage"][-1]
    assert last_tm.status == "error"
    assert "预算" in last_tm.content
    # and it still finished with an answer
    assert msgs[-1].content.startswith("## 报告")


def test_tool_failure_refeeds_model_and_finishes() -> None:
    """Tool exception → the loop catches it into an error ToolMessage; the
    model (scripted) still finishes with a final answer and the event stream
    marks the failed tool honestly."""
    events: list[dict] = []
    msgs = asyncio.run(
        _run_agent(
            [
                {"kind": "tool", "tool": "probe_boom", "args": {}},
                {"kind": "final", "text": "工具失败，无数据可用。换词重试。"},
            ],
            events,
        )
    )
    tr = [e for e in events if e["event"] == "tool_result"]
    assert tr and tr[0]["ok"] is False
    assert "fixture boom" in tr[0]["error"]
    tm = [m for m in msgs if type(m).__name__ == "ToolMessage"]
    assert tm and tm[-1].status == "error"
    assert msgs[-1].content.startswith("工具失败")


def test_max_tool_calls_budget_is_twelve() -> None:
    assert MAX_TOOL_CALLS == 12
