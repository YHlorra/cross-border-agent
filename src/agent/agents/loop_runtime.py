"""Loop runtime driver ( 1.7) — official create_agent execution + final.

Drives one selection run through the create_agent loop and re-emits the
loop's NDJSON wire events + a ``final`` envelope matching the throat's
save_run persistence contract (decision/candidates/report/session_id/run_id)
so the frontend and the persistence layer treat loop runs and legacy graph
runs alike.

Final-answer parsing strategy:
- The agent emits markdown (the SOP report) as its final assistant message.
  The frontend result card is built on a structured envelope, so we run a
  bounded extract step when the agent did not call score_candidates: the
  markdown's 候选表格 rows are converted into ScoredCandidate-shaped dicts
  with the visible scores (per-candidate text such as "(7.2 分, go)").
- When the agent DID use score_candidates, its JSON returns carry the full
  structured candidates and are preferred verbatim.
- The run's seed_keyword (selection_runs / supersedes matching key) is
  captured deterministically from the first tool_call's keyword argument —
  the loop has no intent node, and the SOP's first step is always a search.
This keeps the demo's result card + Listing handoff working under the loop
without sacrificing the "model decides" story.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from ..llm import AimuxChatModel, StartupConfigError, ThinkStreamFilter
from ..state import ScoredCandidate
from .events_middleware import EventsMiddleware
from .listing_agent import build_handoff_tool
from .selection_agent import build_selection_agent
from .tools import MAX_REPORT_CANDIDATES, build_agent_tools, validate_report_payload

log = logging.getLogger(__name__)

# The five section titles the SOP agent prompt fixes (report.md contract).
_SECTIONS = ("市场概览", "候选商品排序", "Top 3 详细分析", "行动建议", "免责声明")

_RECOMMENDATION_LABELS = {"go", "caution", "no-go"}
# markdown table rows only; the legacy dash-bullet format is a
# separate explicit branch (guarded by score+rec presence) so plain bullets
# (行动建议 lines etc.) can no longer become fake candidates.
_TABLE_LINE = re.compile(r"^\s*\|")
_LEGACY_DASH_LINE = re.compile(r"^\s*-\s+")
# Ordered first-hit: "no-go" must be tested before "go" (substring), and
# \b keeps "category"/"logo" from reading as a go.
_REC_PATTERN = re.compile(r"no-go|caution|\bgo\b")
_SEPARATOR_CELL = re.compile(r":?-{2,}:?")
_SCORED_CELL = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*分")
_BARE_SCORE = re.compile(r"[0-9]+(?:\.[0-9]+)?")


def _cells(line: str) -> list[str]:
    core = line.strip()
    if core.startswith("|"):
        core = core[1:]
    if core.endswith("|"):
        core = core[:-1]
    if core.startswith("-"):
        core = core[1:]
    return [c.strip().strip("*").strip() for c in core.split("|")]


def _parse_scored_candidates(markdown: str) -> list[dict]:
    """Best-effort structured candidates from the agent's markdown table.

    Rows we can parse carry a score cell ("N.N 分", or a bare decimal ≤10 —
    prices like 39.99 are out of that band) plus a name cell; rows that
    don't are skipped (the report text itself remains the primary answer).
    When several bare decimals appear (五维得分一览 columns), the one closest
    to the recommendation column wins — 综合得分 conventionally sits there.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for line in markdown.splitlines():
        if _TABLE_LINE.match(line):
            pass  # markdown table row — the SOP's 候选商品排序 format
        elif _LEGACY_DASH_LINE.match(line):
            # legacy bullet "- 名字 (7.2 分, go)": only when it carries both
            # a score and a recommendation label
            if not (_SCORED_CELL.search(line) and _REC_PATTERN.search(line)):
                continue
        else:
            continue
        cells = [c for c in _cells(line)]
        if not cells or not any(cells):
            continue
        if all(_SEPARATOR_CELL.fullmatch(c) for c in cells if c):
            continue

        # score cell: explicit "N.N 分" first, else the bare decimal nearest
        # the recommendation column (≤10 band keeps prices out)
        score: float | None = None
        score_idx = -1
        for i, c in enumerate(cells):
            m = _SCORED_CELL.search(c)
            if m:
                score, score_idx = float(m.group(1)), i
                break
        rec_idx = next(
            (i for i, c in enumerate(cells) if c.lower() in _RECOMMENDATION_LABELS),
            -1,
        )
        if score is None:
            bare = [
                (float(c), i)
                for i, c in enumerate(cells)
                if _BARE_SCORE.fullmatch(c) and 0 < float(c) <= 10
            ]
            if bare:
                if rec_idx >= 0:
                    before = [(v, i) for v, i in bare if i < rec_idx]
                    pick = before[-1] if before else bare[0]
                else:
                    pick = bare[-1]
                score, score_idx = pick
        if score is None:
            continue

        # recommendation: exact cell match, else the ordered first-hit regex
        # on the joined row ("no-go" before "go"; \b guards against
        # "category"/"logo" pollution).
        rec = cells[rec_idx].lower() if rec_idx >= 0 else ""
        if not rec:
            joined = " ".join(cells).lower()
            m = _REC_PATTERN.search(joined)
            rec = m.group(0) if m else ""

        def _clean(c: str) -> str:
            return re.sub(r"\([^)]*\)", "", c).strip()

        # name: first cell with leftover text that is neither the bare score
        # nor the recommendation cell; the score cell itself is the fallback
        # (single-cell dash lines carry "名字 (N.N 分, rec)" in one cell, and
        # the paren strip above already removed the score part).
        name = ""
        for i, c in enumerate(cells):
            if i == score_idx or i == rec_idx:
                continue
            cand = _clean(c)
            if cand and not _BARE_SCORE.fullmatch(cand) and cand.lower() not in _RECOMMENDATION_LABELS:
                name = cand
                break
        if not name and score_idx >= 0:
            cand = _clean(cells[score_idx])
            if cand and not _BARE_SCORE.fullmatch(cand):
                name = cand
        if not name or name in seen:
            continue
        if len(out) >= MAX_REPORT_CANDIDATES:
            break
        seen.add(name)
        out.append(
            {
                "name_cn": name,
                "name_en": name,
                "product_id": name,
                "total_score": score,
                "recommendation": rec,
                "scores": [],
                "opportunities": [],
                "risks": [],
                "source_price_cny": 0.0,
                "target_price_usd": 0.0,
                "price_gap_ratio": 0.0,
                "category": "",
                "review_count": 0,
            }
        )
    return out


def _decision_from(candidates: list[dict], agent_text: str) -> str:
    """Run-level decision: the top candidate's rec; when the text carries no
    candidates, the ordered first-hit regex over the text (a "no-go" report
    must not read as go), else no-go (nothing scored)."""
    if not candidates:
        m = _REC_PATTERN.search(agent_text)
        return m.group(0) if m else "no-go"
    top = max(candidates, key=lambda c: c.get("total_score") or 0)
    return top.get("recommendation") or "no-go"


def _submitted_report(msgs: list[Any]) -> dict | None:
    """The last ``submit_report`` tool_call in the message history, re-validated
    through the same truth source as the tool itself — args, not the
    ToolMessage, so a hallucinated success string can't become the final
    envelope. Returns {candidates, decision, seed_keyword} or None."""
    from langchain_core.messages import AIMessage

    for m in reversed(msgs):
        if not isinstance(m, AIMessage):
            continue
        for call in reversed(getattr(m, "tool_calls", None) or []):
            if call.get("name") != "submit_report":
                continue
            args = call.get("args") or {}
            try:
                rows = json.loads(args.get("candidates_json") or "")
            except json.JSONDecodeError:
                continue
            decision = str(args.get("decision") or "")
            scored, err = validate_report_payload(
                rows if isinstance(rows, list) else [], decision
            )
            if err is not None:
                continue
            return {
                "candidates": [s.model_dump() for s in scored],
                "decision": decision,
                "seed_keyword": str(args.get("seed_keyword") or ""),
            }
    return None


def _final_envelope(
    *,
    agent_text: str,
    candidates: list[dict],
    session_id: str,
    run_id: str,
    seed_keyword: str = "",
    decision: str | None = None,
) -> dict[str, Any]:
    decision = decision or _decision_from(candidates, agent_text)
    top3 = candidates[:3]
    report = {
        "session_id": session_id,
        "seed_keyword": seed_keyword,
        "market_summary": agent_text,
        "top_recommendations": [
            {
                "product_id": c.get("product_id", ""),
                "opportunities": c.get("opportunities", []),
                "risks": c.get("risks", []),
                "cut_in_direction": (c.get("opportunities") or [""])[0],
            }
            for c in top3
        ],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "event": "final",
        "decision": decision,
        "report": report,
        "candidates": candidates,
        "market_summary": agent_text,
        "session_id": session_id,
        "run_id": run_id,
    }


async def stream_run_loop(
    *,
    query: str,
    session_id: str,
    run_id: str,
    history_context: str,
    agents_models: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """Drive one create_agent run; yields wire-format events (the loop's own
    middleware events + the final envelope). Persistence happens in the caller
    (server throat) — this generator is pure wire.
    """
    model = agents_models.get("selection")
    if model is None:
        raise StartupConfigError(["selection model"])
    # The loop needs an aimux→BaseChatModel adapter (create_agent contract).
    # Two cases:
    #  - real deployments pass the graph-runtime AimuxChatModel (llm.py) →
    #    wrap in AimuxLangchainAdapter
    #  - SEAM tests pass a FakeChatModel (a BaseChatModel already) → use as-is
    from langchain_core.language_models.chat_models import BaseChatModel as _BCM
    from ..llm_langchain import AimuxLangchainAdapter

    adapter_model = (
        model
        if isinstance(model, _BCM) and not isinstance(model, AimuxChatModel)
        else AimuxLangchainAdapter(model)
    )
    tools = [
        *build_agent_tools(
            model=model if isinstance(model, AimuxChatModel) else None
        ),
        # handoff_to_listing is a selection-registry extra (not part
        # of build_agent_tools): the listing agent's own registry stays
        # submit_draft-only, and component tests inject it the same way.
        build_handoff_tool(),
    ]
    events_queue: asyncio.Queue = asyncio.Queue()
    agent = build_selection_agent(
        model=adapter_model,
        tools=tools,
        events_queue=events_queue,
        history_context=history_context,
    )

    # Kick off the run and drain events from the middleware queue.
    async def _drive() -> dict[str, Any]:
        from langchain_core.messages import HumanMessage

        out = await agent.ainvoke({"messages": [HumanMessage(content=query)]})
        return out["messages"]

    task = asyncio.create_task(_drive())
    try:
        tool_result_payloads: list[dict] = []
        # decode the loop's score results from the message history (full
        # JSON; the wire tool_result summary is display-truncated).
        scored_tool_messages: list[Any] = []
        # seed_keyword: first search/score tool_call's keyword arg (deterministic
        # — the intent-node equivalent for the loop runtime).
        seed_keyword = ""
        while True:
            try:
                ev = await asyncio.wait_for(events_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                if task.done():
                    break
                continue
            if ev.get("event") == "tool_result" and ev.get("ok"):
                tool_result_payloads.append(ev)
            if ev.get("event") == "tool_call" and not seed_keyword:
                args = ev.get("args") or {}
                if ev.get("tool") == "score_candidates":
                    seed_keyword = str(args.get("seed_keyword") or "")
                else:
                    seed_keyword = str(args.get("keyword") or "")
            yield ev

        msgs = task.result()
        for m in msgs:
            from langchain_core.messages import ToolMessage

            if isinstance(m, ToolMessage) and getattr(m, "name", None) == "score_candidates":
                scored_tool_messages.append(m)
        agent_text = ""
        for m in reversed(msgs):
            from langchain_core.messages import AIMessage

            if isinstance(m, AIMessage) and m.content:
                agent_text = m.content if isinstance(m.content, str) else ""
                break

        # Terminal-state source priority ( 2.3):
        # ① submit_report tool_call args (structured exit, re-validated via
        #    the tool's own validator) →
        # ② score_candidates ToolMessage payloads (message-history JSON, not
        #    the display-truncated wire summary) →
        # ③ the repaired markdown fallback.
        submitted = _submitted_report(msgs)
        decision: str | None = None
        if submitted is not None:
            candidates = submitted["candidates"]
            decision = submitted["decision"]
            seed_keyword = submitted["seed_keyword"] or seed_keyword
        else:
            candidates = []
            for m in reversed(scored_tool_messages):
                try:
                    parsed = json.loads(m.content) if isinstance(m.content, str) else []
                except json.JSONDecodeError:
                    continue
                rows = parsed if isinstance(parsed, list) else []
                if rows:
                    candidates = [r for r in rows if isinstance(r, dict)]
                    break
            if not candidates:
                candidates = _parse_scored_candidates(agent_text)

        yield _final_envelope(
            agent_text=agent_text,
            candidates=candidates,
            session_id=session_id,
            run_id=run_id,
            seed_keyword=seed_keyword,
            decision=decision,
        )
    finally:
        # Client stopped / disconnected before the run finished: without this
        # the orphan task keeps driving the whole agent loop (LLM spend)
        # behind a dead response. Turn-boundary cancel semantics — the
        # in-flight model call finishes and is discarded, no further turns.
        if not task.done():
            task.cancel()
            log.info("selection run %s cancelled by client", run_id)
        # Retrieve the outcome so asyncio never logs "exception was never
        # retrieved" (cancelled tasks must not call .exception).
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())


# ─── token-streaming driver ────────────────────────────────


async def stream_run_loop_tokens(
    *,
    query: str,
    session_id: str,
    run_id: str,
    history_context: str,
    agents_models: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """Streaming driver — same final envelope as ``stream_run_loop`` but
    the model runs through ``agent.astream(stream_mode="messages")`` so
    AIMessageChunk tokens flow as ``agent_delta{delta}`` events, and a
    ThinkStreamFilter sink emits ``thinking{delta}`` for the dropped
    reasoning blocks.

    The middleware still owns turn_start / turn_end / tool_call /
    tool_result. We add a second driver loop that interleaves the
    events_queue (middleware) with the chunk iterator (per-token text).

    Risk (D7): chunk vs queue ordering under load is not proven. The
    ainvoke fallback in ``stream_run_loop`` is kept untouched; if a
    consumer wants astream semantics it opts in via this entry point.
    """
    from langchain_core.messages import HumanMessage

    model = agents_models.get("selection")
    if model is None:
        raise StartupConfigError(["selection model"])

    from langchain_core.language_models.chat_models import BaseChatModel as _BCM
    from ..llm_langchain import AimuxLangchainAdapter

    adapter_model = (
        model
        if isinstance(model, _BCM) and not isinstance(model, AimuxChatModel)
        else AimuxLangchainAdapter(model)
    )
    tools = [
        *build_agent_tools(
            model=model if isinstance(model, AimuxChatModel) else None
        ),
        build_handoff_tool(),
    ]
    events_queue: asyncio.Queue = asyncio.Queue()

    # Build the agent with a per-instance EventsMiddleware so we can call
    # set_thinking_sink on it after construction.
    from ..prompts import load_selection_system_prompt
    system_prompt = load_selection_system_prompt()
    if history_context and history_context.strip():
        system_prompt = (
            f"{system_prompt}\n\n## 历史上下文（上一轮选品结果，追问的指代对象）"
            f"\n{history_context.strip()}"
        )
    from langchain.agents import create_agent
    thinking_mw = EventsMiddleware(
        events_queue.put, emit_text_deltas=False
    )

    agent = create_agent(
        model=adapter_model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=[thinking_mw],
    )

    # Wire the think-block sink to a coroutine that forwards a
    # ``thinking`` wire event per closed think block.
    current_turn = {"i": 0}

    async def _thinking_sink(_turn_idx: int, text: str) -> None:
        if not text:
            return
        await events_queue.put(
            {"event": "thinking", "turn": _turn_idx, "delta": text}
        )

    thinking_mw.set_thinking_sink(_thinking_sink)

    # Per-token text filter (no sink) — strips think blocks from the
    # chunk stream before we yield agent_delta events.
    safe_filter = ThinkStreamFilter(sink=None)

    async def _drive_chunks():
        """Read per-message chunks, filter think blocks, yield safe deltas."""
        try:
            async for chunk, _meta in agent.astream(
                {"messages": [HumanMessage(content=query)]},
                stream_mode="messages",
            ):
                content = getattr(chunk, "content", "")
                if isinstance(content, list):
                    text = "".join(
                        item.get("text", "")
                        for item in content
                        if isinstance(item, dict)
                    )
                else:
                    text = content if isinstance(content, str) else ""
                if text:
                    safe = safe_filter.feed(text)
                    if safe:
                        yield {
                            "event": "agent_delta",
                            "delta": safe,
                            "turn": current_turn["i"],
                        }
        finally:
            leftover = safe_filter.flush()
            if leftover:
                yield {
                    "event": "agent_delta",
                    "delta": leftover,
                    "turn": current_turn["i"],
                }

    chunk_iter = _drive_chunks()
    try:
        while True:
            try:
                ev = events_queue.get_nowait()
            except asyncio.QueueEmpty:
                try:
                    ev = await asyncio.wait_for(chunk_iter.__anext__(), timeout=0.05)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    if events_queue.empty():
                        break
                    continue
            if ev.get("event") == "turn_start":
                current_turn["i"] = ev.get("turn", current_turn["i"] + 1)
                safe_filter._suppressing = False
                safe_filter._buf = ""
            yield ev
    finally:
        # astream generator self-terminates; no task to cancel.
        pass
