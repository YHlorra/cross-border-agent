"""SEAM — listing agent handoff + driver contract (FakeChatModel).

Two scenarios:
- handoff tool: the selection registry gains handoff_to_listing; a scripted
  model calls it with a valid candidate and the driver records the call.
- listing agent driver: a scripted model submits a draft through the
  submit_draft tool → the validation layer auto-fixes issues → the loop
  emits listing_draft + final (kind=listing) with the SAME wire shape the
  listing graph used (listing_runs contract unchanged).
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

from agent.agents.listing_agent import (
    build_handoff_tool,
    build_listing_tools,
    build_listing_agent,
    stream_run_listing_loop,
)
from agent.agents.selection_agent import build_selection_agent
from agent.agents.tools import build_agent_tools


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


_CANDIDATE = {
    "product_id": "1688_004",
    "name_cn": "宠物自动喂食器",
    "name_en": "Smart Pet Feeder",
    "source_price_cny": 198.0,
    "target_price_usd": 39.99,
    "price_gap_ratio": 1.44,
    "category": "pet_supplies",
    "review_count": 210,
}


def test_handoff_tool_rejects_missing_fields() -> None:
    h = build_handoff_tool()
    raw = h.invoke({"candidate_json": json.dumps({"name_cn": "x"}, ensure_ascii=False)})
    out = json.loads(raw)
    assert "error" in out and "missing" in out["error"]


def test_handoff_tool_records_valid_candidate() -> None:
    h = build_handoff_tool()
    raw = h.invoke({"candidate_json": json.dumps(_CANDIDATE, ensure_ascii=False)})
    out = json.loads(raw)
    assert out["handoff"] == "listing"
    assert out["candidate"]["product_id"] == "1688_004"


def test_selection_agent_can_call_handoff() -> None:
    """The selection agent's registry + handoff tool compose; a scripted model
    that chooses handoff completes the loop with the recorded payload."""
    events: list[dict] = []
    model = Scripted(
        [
            {
                "kind": "tool",
                "tool": "handoff_to_listing",
                "args": {"candidate_json": json.dumps(_CANDIDATE, ensure_ascii=False)},
            },
            {"kind": "final", "text": "已转入 Listing 生成。"},
        ]
    )
    tools = [*build_agent_tools(), build_handoff_tool()]
    agent = build_selection_agent(model=model, tools=tools)

    async def _go() -> list:
        out = await agent.ainvoke({"messages": [HumanMessage(content="帮这个生成 Listing")]})
        return out["messages"]

    msgs = asyncio.run(_go())
    # the handoff tool result was fed back to the model (message history has
    # the tool message with the recorded payload)
    tm = [m for m in msgs if type(m).__name__ == "ToolMessage"]
    assert tm and json.loads(tm[0].content)["handoff"] == "listing"


def test_submit_draft_validates_and_autofixes() -> None:
    """The submit_draft tool runs the real validate_listing layer: an
    over-length title comes back auto-fixed inside 75 chars with an issue."""
    tools = build_listing_tools()
    submit = next(t for t in tools if t.name == "submit_draft")
    draft = {
        "item_name": "Smart Pet Feeder with Camera 4L Auto Dispenser Extra Long Title That Definitely Exceeds Seventy Five Characters",
        "bullet_point": [
            "Smart pet feeder with built-in camera and 4L capacity for cats and dogs",
            "Schedule meals from your phone while you are away from home",
            "Made of food-safe stainless steel bowl and durable ABS body",
            "Package includes one feeder unit one power adapter and one user manual",
            "Works with 2.4GHz WiFi networks for remote control",
        ],
        "product_description": "A smart pet feeder that keeps your pet fed on time.",
        "generic_keyword": "auto pet feeder cat dog dispenser",
    }
    raw = submit.invoke({"listing_json": json.dumps(draft, ensure_ascii=False)})
    out = json.loads(raw)
    assert "hard_failed" in out
    assert len(out["output"]["item_name"]) <= 75
    assert len(out["output"]["bullet_point"]) == 5
    # the truncated title issue was recorded as auto_fixed
    titles = [i for i in out["issues"] if i.get("field") == "item_name"]
    assert titles and any(i.get("severity") == "auto_fixed" for i in titles)


def _listing_draft():
    return {
        "item_name": "Smart Pet Feeder with Camera 4L Auto Dispenser for Cats Dogs",
        "bullet_point": [
            "Built-in 1080p camera lets you watch your pet from anywhere",
            "Schedule up to 4 meals daily with portion control from the app",
            "Food-safe stainless steel bowl with anti-jam auger design",
            "Low food and low battery alerts keep you informed at all times",
            "Works with 2.4GHz WiFi and supports multi-pet recognition",
        ],
        "product_description": "Never worry about missed meals again. The smart feeder keeps your pet on a healthy schedule.",
        "generic_keyword": "automatic pet feeder camera app controlled",
    }


def test_listing_agent_driver_emits_listing_wire() -> None:
    """A scripted listing agent that submits a clean draft → driver emits the
    listing dialect (node_start draft → listing_draft → final kind=listing)."""
    events: list[dict] = []
    model = Scripted(
        [
            {
                "kind": "tool",
                "tool": "submit_draft",
                "args": {"listing_json": json.dumps(_listing_draft(), ensure_ascii=False)},
            },
            {"kind": "final", "text": "DRAFT_OK"},
        ]
    )

    async def _go() -> list[dict]:
        out: list[dict] = []
        async for ev in stream_run_listing_loop(
            candidate=_CANDIDATE,
            market_code="US",
            brand="",
            competitor_brands=["PetSafe"],
            session_id="s1",
            run_id="r-l1",
            agents_models={"listing": model},
        ):
            out.append(ev)
        return out

    events = asyncio.run(_go())
    kinds = [e.get("event") for e in events]
    assert kinds[0] == "node_start"
    assert "listing_draft" in kinds
    assert kinds[-1] == "final"
    final = events[-1]
    assert final["kind"] == "listing"
    assert final["run_id"] == "r-l1"
    assert final["session_id"] == "s1"
    assert final["product_id"] == "1688_004"
    assert final["hard_failed"] is False
    assert final["listing"]["item_name"].startswith("Smart Pet Feeder")


# ── cancel path ( — client stops mid-draft) ───────────────────────────────


class HangingModel(BaseChatModel):
    """Same executor-thread sentinel as tests/test_loop_runtime.py: _generate
    blocks on a threading.Event until the test releases it."""

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


def test_listing_driver_cancel_closes_and_logs(caplog) -> None:
    """Client stop mid-draft → aclose cancels the in-flight listing loop and
    logs it (assert the cancel log, not a generator sentinel)."""
    release = threading.Event()
    model = HangingModel(release=release)

    async def _scenario() -> None:
        gen = stream_run_listing_loop(
            candidate=_CANDIDATE,
            market_code="US",
            brand="",
            competitor_brands=[],
            session_id="s1",
            run_id="r-l-cancel",
            agents_models={"listing": model},
        )
        first = await gen.__anext__()
        assert first == {"event": "node_start", "node": "draft"}
        await gen.aclose()
        release.set()
        await asyncio.sleep(0.2)

    caplog.set_level(logging.INFO, logger="agent.agents.listing_agent")
    t0 = time.monotonic()
    try:
        asyncio.run(_scenario())
    finally:
        release.set()
    assert time.monotonic() - t0 < 1.0
    assert "listing run r-l-cancel cancelled by client" in caplog.text
