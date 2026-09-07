"""general-agent-chat SEAM — general loop driver + domain tools with a
scripted model. No LLM/network, no SQLite (persist hooks are recorders).

Covers the wire contract of /chat's driver (stream_general_loop): plain-text
chat final (kind:"chat"), intent-triggered run_selection (card event +
persisted-through-hooks nested run), honest empty selection (no card), the
listing tool contract (missing-key error, card + hook), and the chat history
context builder.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import agent.agents.general_agent as ga
from agent.agents.general_agent import (
    build_domain_tools,
    stream_general_loop,
)
from agent.server import _chat_history_context


class Scripted(BaseChatModel):
    """Same scripting dialect as test_loop_runtime: one step per model turn."""

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


class SystemCapturing(Scripted):
    """Scripted + captures the first (system) message the model receives —
    proves the slash appendix reached the prompt assembly, not just the file."""

    first_system: str | None = None

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.first_system is None and messages:
            self.first_system = str(messages[0].content)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class Recorder:
    """PersistHooks double — records instead of touching the store."""

    def __init__(self) -> None:
        self.selections: list[dict] = []
        self.listings: list[dict] = []

    def selection_run(self, *, query, final, run_id, session_id):
        self.selections.append(
            {"query": query, "final": final, "run_id": run_id, "session_id": session_id}
        )

    def listing_run(self, *, candidate, final, run_id, session_id):
        self.listings.append(
            {"candidate": candidate, "final": final, "run_id": run_id, "session_id": session_id}
        )


class EventSink:
    """Async emit double — matches the driver's queue-put contract."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def __call__(self, ev: dict) -> None:
        self.events.append(ev)


_CANDIDATE = {
    "product_id": "1688_001",
    "name_cn": "宠物自动喂食器",
    "name_en": "Automatic Pet Feeder",
    "source_price_cny": 42.0,
    "target_price_usd": 32.99,
    "price_gap_ratio": 5.4,
    "category": "pet_supplies",
    "review_count": 120,
    "total_score": 8.1,
    "recommendation": "go",
    "scores": [],
    "opportunities": ["复购型耗材"],
    "risks": [],
}


def _fake_selection_stream(*, candidates: list[dict], decision: str = "go"):
    """stream_run_loop double — yields the wire shape the real driver emits."""

    def _factory(*, query, session_id, run_id, history_context, agents_models):
        async def gen():
            yield {"event": "turn_start", "turn": 1, "tool_calls_used": 0}
            yield {"event": "tool_call", "tool": "search_1688", "args": {"keyword": query}, "turn": 1}
            yield {"event": "tool_result", "tool": "search_1688", "ok": True, "duration_ms": 5, "summary": "[]"}
            if not candidates:
                yield {"event": "empty", "keyword": query, "message": "没有匹配的候选"}
            yield {
                "event": "final",
                "decision": decision if candidates else "no-go",
                "candidates": candidates,
                "report": {"seed_keyword": query, "market_summary": "摘要"},
                "market_summary": "摘要",
                "session_id": session_id,
                "run_id": run_id,
            }

        return gen()

    return _factory


def _fake_listing_stream(*, ok: bool = True):
    def _factory(*, candidate, market_code, brand, competitor_brands, session_id, run_id, agents_models):
        async def gen():
            yield {"event": "node_start", "node": "draft"}
            if not ok:
                yield {"event": "error", "code": "NoListingDraft", "message": "未产出合规草稿"}
                return
            yield {
                "event": "final",
                "kind": "listing",
                "run_id": run_id,
                "product_id": candidate.get("product_id", ""),
                "market_code": market_code,
                "listing": {"item_name": "Automatic Pet Feeder 5L", "bullet_point": [], "product_description": "", "generic_keyword": ""},
                "issues": [],
                "hard_failed": False,
                "session_id": session_id,
            }

        return gen()

    return _factory


async def _drive_chat(
    script: list[dict], persist: Any, query: str = "你好"
) -> list[dict]:
    events: list[dict] = []
    async for ev in stream_general_loop(
        query=query,
        session_id="s1",
        run_id="r1",
        history_context="",
        agents_models={"selection": Scripted(script)},
        persist=persist,
    ):
        events.append(ev)
    return events


# ── driver ───────────────────────────────────────────────────────────────────


def test_general_plain_text_yields_chat_final_without_tools() -> None:
    """The bug-fix contract: a plain utterance produces a text answer — no
    tool calls, no selection card, no pipeline side effects."""
    events = asyncio.run(
        _drive_chat([{"kind": "final", "text": "你好，我是店长助手"}], Recorder())
    )
    kinds = [e.get("event") for e in events]
    assert kinds[-1] == "final"
    assert events[-1]["kind"] == "chat"
    assert events[-1]["text"] == "你好，我是店长助手"
    assert events[-1]["session_id"] == "s1" and events[-1]["run_id"] == "r1"
    assert "tool_call" not in kinds
    assert "card" not in kinds


def test_general_intent_calls_run_selection_emits_card_and_persists() -> None:
    rec = Recorder()
    original = ga.stream_run_loop
    ga.stream_run_loop = _fake_selection_stream(candidates=[dict(_CANDIDATE)])
    try:
        events = asyncio.run(
            _drive_chat(
                [
                    {"kind": "tool", "tool": "run_selection", "args": {"query": "宠物自动喂食器 预算80"}},
                    {"kind": "final", "text": "已为你完成选品，卡片见上。"},
                ],
                rec,
            )
        )
    finally:
        ga.stream_run_loop = original

    kinds = [e.get("event") for e in events]
    assert "tool_call" in kinds and "tool_result" in kinds
    cards = [e for e in events if e.get("event") == "card"]
    assert len(cards) == 1
    card = cards[0]["card"]
    assert card["type"] == "selection"
    assert card["decision"] == "go"
    assert card["candidates"][0]["product_id"] == "1688_001"
    assert card["report"]["seed_keyword"]
    # nested run persisted through the hooks (throat contract), not dropped
    assert len(rec.selections) == 1
    assert rec.selections[0]["query"] == "宠物自动喂食器 预算80"
    assert rec.selections[0]["session_id"] == "s1"
    assert rec.selections[0]["run_id"] == card["run_id"]
    # chat final still closes the run
    assert events[-1]["event"] == "final" and events[-1]["kind"] == "chat"


# ── slash → skill injection (Codex-style,  Addendum) ────────────────


def test_slash_skill_appendix_routes_known_tokens() -> None:
    """：已知 token（zh label + latin id，大小写不敏感）→ 对应 skill
    附录；普通文本 / 未知 token → None。"""
    from agent.prompts import slash_skill_appendix

    sel = slash_skill_appendix("/选品 无线耳机 预算 300")
    assert sel is not None
    assert "选品方法论" in sel and "run_selection" in sel
    listing_app = slash_skill_appendix("/listing 自动喂食器")
    assert listing_app is not None
    assert "Listing 文案方法论" in listing_app and "save_listing" in listing_app
    assert slash_skill_appendix("/Selection x") is not None  # latin id + 大小写
    assert slash_skill_appendix("帮我选品") is None
    assert slash_skill_appendix("/help") is None


async def _drive_capturing(model: SystemCapturing, query: str) -> list[dict]:
    events: list[dict] = []
    async for ev in stream_general_loop(
        query=query,
        session_id="s1",
        run_id="r1",
        history_context="",
        agents_models={"selection": model},
        persist=Recorder(),
    ):
        events.append(ev)
    return events


def test_stream_general_loop_injects_appendix_for_slash_only() -> None:
    """装配契约：/选品 查询 → system prompt 含附录 + skill 正文；普通查询 →
    只有 chat.md，无附录（prompt 预算纪律）。"""
    for query, injected in (("/选品 空气炸锅 预算 500", True), ("你好", False)):
        model = SystemCapturing([{"kind": "final", "text": "好的"}])
        events = asyncio.run(_drive_capturing(model, query))
        assert events[-1]["event"] == "final"
        assert model.first_system is not None
        assert ("附录：slash 命令注入" in model.first_system) is injected
        assert ("平台与语言" in model.first_system) is injected


# ── domain tools (direct) ────────────────────────────────────────────────────


def test_run_selection_empty_emits_no_card_and_reports_honestly() -> None:
    sink = EventSink()
    rec = Recorder()
    original = ga.stream_run_loop
    ga.stream_run_loop = _fake_selection_stream(candidates=[])
    try:
        (tool,) = [
            t
            for t in build_domain_tools(
                emit=sink, session_id="s1", agents_models={}, persist=rec
            )
            if t.name == "run_selection"
        ]
        result = asyncio.run(tool.coroutine(query="冷门品类"))
    finally:
        ga.stream_run_loop = original

    parsed = json.loads(result)
    assert parsed["ok"] is True and parsed["count"] == 0
    assert parsed["message"]
    assert sink.events == []  # no card for an empty result
    assert rec.selections  # the empty run is still persisted


def test_run_listing_rejects_incomplete_candidate() -> None:
    sink = EventSink()
    rec = Recorder()
    (tool,) = [
        t
        for t in build_domain_tools(
            emit=sink, session_id="s1", agents_models={}, persist=rec
        )
        if t.name == "run_listing"
    ]
    result = asyncio.run(tool.coroutine(candidate_json=json.dumps({"name_cn": "喂食器"})))
    parsed = json.loads(result)
    assert "error" in parsed and "product_id" in parsed["error"]
    assert sink.events == [] and rec.listings == []


def test_run_listing_success_emits_card_and_persists() -> None:
    sink = EventSink()
    rec = Recorder()
    original = ga.stream_run_listing_loop
    ga.stream_run_listing_loop = _fake_listing_stream(ok=True)
    try:
        (tool,) = [
            t
            for t in build_domain_tools(
                emit=sink, session_id="s1", agents_models={}, persist=rec
            )
            if t.name == "run_listing"
        ]
        result = asyncio.run(
            tool.coroutine(candidate_json=json.dumps(dict(_CANDIDATE)))
        )
    finally:
        ga.stream_run_listing_loop = original

    parsed = json.loads(result)
    assert parsed["ok"] is True and parsed["title_preview"].startswith("Automatic")
    assert len(sink.events) == 1
    card = sink.events[0]["card"]
    assert card["type"] == "listing"
    assert card["listing"]["item_name"] == "Automatic Pet Feeder 5L"
    assert card["candidate"]["product_id"] == "1688_001"
    assert len(rec.listings) == 1
    assert rec.listings[0]["run_id"] == card["run_id"]


def test_run_listing_pipeline_failure_returns_error_not_card() -> None:
    sink = EventSink()
    rec = Recorder()
    original = ga.stream_run_listing_loop
    ga.stream_run_listing_loop = _fake_listing_stream(ok=False)
    try:
        (tool,) = [
            t
            for t in build_domain_tools(
                emit=sink, session_id="s1", agents_models={}, persist=rec
            )
            if t.name == "run_listing"
        ]
        result = asyncio.run(
            tool.coroutine(candidate_json=json.dumps(dict(_CANDIDATE)))
        )
    finally:
        ga.stream_run_listing_loop = original

    assert "error" in json.loads(result)
    assert sink.events == [] and rec.listings == []


# ── save_listing CLI（：自由起草的 Listing 入列表） ────────────────


def _drafted_listing() -> dict:
    """一份按 copywriting skill 起草、能通过硬校验的 Listing JSON。"""
    return {
        "item_name": "Automatic Pet Feeder 5L Timer Stainless Steel Clog-Free",
        "bullet_point": [
            "Holds 5L of dry food for up to three weeks between refills",
            "Stainless steel bowl is removable and dishwasher safe",
            "Programmable timer serves up to four meals per day",
            "Clog-free auger works with kibble up to 15mm wide",
            "Dual power supply with battery backup keeps meals on time",
        ],
        "product_description": (
            "A large-capacity automatic feeder for cats and small dogs. "
            "Programmable portions keep feeding consistent while you travel."
        ),
        "generic_keyword": "wifi app control splash proof",
    }


def test_save_listing_success_persists_and_emits_card() -> None:
    sink = EventSink()
    rec = Recorder()
    (tool,) = [
        t
        for t in build_domain_tools(
            emit=sink, session_id="s1", agents_models={}, persist=rec
        )
        if t.name == "save_listing"
    ]
    result = asyncio.run(
        tool.coroutine(
            listing_json=json.dumps(_drafted_listing()),
            candidate_json=json.dumps(dict(_CANDIDATE)),
        )
    )
    parsed = json.loads(result)
    assert parsed["ok"] is True and parsed["hard_failed"] is False
    assert parsed["issues"] == []
    assert len(sink.events) == 1
    card = sink.events[0]["card"]
    assert card["type"] == "listing"
    assert card["candidate"]["product_id"] == "1688_001"
    assert len(rec.listings) == 1
    assert rec.listings[0]["run_id"] == card["run_id"]
    assert rec.listings[0]["candidate"]["product_id"] == "1688_001"


def test_save_listing_synthesizes_minimal_candidate_when_absent() -> None:
    sink = EventSink()
    rec = Recorder()
    (tool,) = [
        t
        for t in build_domain_tools(
            emit=sink, session_id="s1", agents_models={}, persist=rec
        )
        if t.name == "save_listing"
    ]
    result = asyncio.run(
        tool.coroutine(listing_json=json.dumps(_drafted_listing()))
    )
    parsed = json.loads(result)
    assert parsed["ok"] is True
    assert rec.listings[0]["candidate"]["product_id"].startswith("manual-")


def test_save_listing_hard_fail_still_persists_and_reports_issues() -> None:
    """违禁语草稿：落库但 hard_failed=true + issues 回传，agent 按 issues
    修正后重新保存——校验层兜底，不让 agent 的自由起草绕过平台规则。"""
    sink = EventSink()
    rec = Recorder()
    (tool,) = [
        t
        for t in build_domain_tools(
            emit=sink, session_id="s1", agents_models={}, persist=rec
        )
        if t.name == "save_listing"
    ]
    bad = dict(_drafted_listing(), item_name="Pet Feeder 50% off Best Price")
    result = asyncio.run(tool.coroutine(listing_json=json.dumps(bad)))
    parsed = json.loads(result)
    assert parsed["ok"] is True and parsed["hard_failed"] is True
    assert any(i["code"] == "title_forbidden_phrase" for i in parsed["issues"])
    assert len(sink.events) == 1 and len(rec.listings) == 1


def test_save_listing_rejects_malformed_json() -> None:
    sink = EventSink()
    rec = Recorder()
    (tool,) = [
        t
        for t in build_domain_tools(
            emit=sink, session_id="s1", agents_models={}, persist=rec
        )
        if t.name == "save_listing"
    ]
    result = asyncio.run(tool.coroutine(listing_json="not json"))
    assert "error" in json.loads(result)
    assert sink.events == [] and rec.listings == []


# ── chat history context (, pure) ─────────────────────────────────────────


def test_chat_history_context_builds_transcript_and_excludes_listing_turns() -> None:
    events = [
        {"event_type": "user_message", "payload": {"query": "你好", "kind": "chat_turn"}},
        {"event_type": "final", "payload": {"kind": "chat", "text": "你好呀，需要什么帮助？"}},
        # selection runs keep their own channel — only the user line joins
        {"event_type": "user_message", "payload": {"query": "帮我找喂食器", "history_run_id": None}},
        {"event_type": "final", "payload": {"decision": "go", "candidates": [{}]}},
        # listing turns are NOT chat history
        {"event_type": "user_message", "payload": {"query": "生成listing", "kind": "listing_turn"}},
        {"event_type": "final", "payload": {"kind": "listing", "listing": {}}},
    ]
    ctx = _chat_history_context(events)
    assert "用户: 你好" in ctx
    assert "助手: 你好呀，需要什么帮助？" in ctx
    assert "用户: 帮我找喂食器" in ctx
    assert "生成listing" not in ctx
    assert "kind" not in ctx  # wire payloads never leak raw


def test_chat_history_context_caps_length() -> None:
    events = [
        {"event_type": "final", "payload": {"kind": "chat", "text": "x" * 9000}}
    ]
    assert len(_chat_history_context(events)) <= 4000
