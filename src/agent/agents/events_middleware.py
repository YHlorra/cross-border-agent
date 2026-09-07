"""NDJSON events middleware ( 1.5) — turn/tool process events + budget guard.

The middleware converts the create_agent loop's internal steps into the same
wire-event dialect the rest of the project consumes (wire only adds
events — `turn_start` / `agent_delta` / `tool_result` join the existing
node_*/tool_call/report_chunk/final vocabulary). The budget counter
≤12 tool calls / ≤8 model turns) lives here too: when exhausted it either
short-circuits the next tool call with an honest error ToolMessage (forcing
the model to finish with the data it has) and injects a budget warning into
the following model request.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

# Budget knobs (design.md D3). A run is capped at 12 tool calls / 8 model
# turns; hitting the cap forces a graceful finish — never a silent loop.
MAX_TOOL_CALLS = 12
MAX_TURNS = 8

_WARNING = "预算将尽（本轮已达工具调用上限）：立即用现有数据输出最终报告，不要再调用任何工具。"


def _emitted_summary(content: Any, max_chars: int = 120) -> str:
    """Short human-readable summary of a tool result for the wire."""
    if isinstance(content, str):
        text = content
    else:
        text = json.dumps(content, ensure_ascii=False)
    text = " ".join(text.split())
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


class EventsMiddleware(AgentMiddleware):
    """Middleware that emits NDJSON wire events for the create_agent loop.

    Usage: ``create_agent(..., middleware=[EventsMiddleware(emit)])`` where
    ``emit`` is an async callable receiving one wire-event dict (the same
    sink the graph nodes' events_queue feeds).

    ``emit_text_deltas`` toggle. When False,
    the post-turn ``agent_delta`` emission at the end of ``awrap_model_call``
    is suppressed (the streaming path owns per-token deltas). turn/tool
    events are unaffected.
    """

    def __init__(
        self,
        emit: Any,
        *,
        budget_warning: str = _WARNING,
        emit_text_deltas: bool = True,
    ) -> None:
        self._emit = emit
        self._budget_warning = budget_warning
        self._emit_text_deltas = emit_text_deltas
        self._tool_calls = 0
        self._turns = 0
        # sink for "thinking" wire events. The streaming driver
        # wires this up after construction (so the middleware remains
        # synchronous; the stream can pass a coroutine sink that respects
        # the same async boundary). None means "do not emit thinking events"
        # — preserves the pre-streaming wire contract.
        self._thinking_sink: Any = None
        super().__init__()

    def set_thinking_sink(self, sink: Any) -> None:
        """Wire an async sink that the streaming driver will call with
        (turn_index, reasoning_text) pairs. Pass None to disable."""
        self._thinking_sink = sink

    async def _emit_thinking(self, turn: int, delta: str) -> None:
        if self._thinking_sink is None or not delta:
            return
        try:
            await self._thinking_sink(turn, delta)
        except Exception:  # noqa: BLE001
            pass

    async def _emit_async(self, payload: dict) -> None:
        try:
            if self._emit is not None:
                await self._emit(payload)
        except Exception:  # noqa: BLE001 — event emission must never break the agent
            pass

    async def abefore_agent(self, state: Any, runtime: Any) -> dict | None:
        self._turns = 0
        self._tool_calls = 0
        return None

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """Emit turn_start before each model call; warn when budget is spent."""
        self._turns += 1
        turn = self._turns
        if self._tool_calls >= MAX_TOOL_CALLS or self._turns > MAX_TURNS:
            # Inject the budget warning into the conversation so the model
            # sees it before deciding to call another tool (it will get a
            # blocked ToolMessage anyway if it tries).
            request = request.override(
                messages=[*request.messages, SystemMessage(content=self._budget_warning)]
            )
        await self._emit_async(
            {
                "event": "turn_start",
                "turn": turn,
                "tool_calls_used": self._tool_calls,
            }
        )
        try:
            resp = await handler(request)
        except Exception:
            await self._emit_async(
                {
                    "event": "turn_end",
                    "turn": turn,
                    "error": True,
                }
            )
            raise
        # Emit agent_delta for any text the model produced this turn (final
        # answers stream out as deltas; tool-call turns carry empty text).
        # suppressed when emit_text_deltas=False (streaming path
        # owns per-token deltas via the loop_runtime's ThinkStreamFilter).
        if self._emit_text_deltas:
            msgs = resp.result if hasattr(resp, "result") else []
            for m in msgs:
                if isinstance(m, AIMessage):
                    text = m.content if isinstance(m.content, str) else ""
                    calls = getattr(m, "tool_calls", None) or []
                    if text and not calls:
                        await self._emit_async(
                            {"event": "agent_delta", "delta": text, "turn": turn}
                        )
        await self._emit_async(
            {"event": "turn_end", "turn": turn, "tool_calls": bool(getattr(resp, "result", []) and _last_has_tool_calls(resp.result))}
        )
        return resp

    async def awrap_tool_call(self, request: ToolCallRequest, handler: Any) -> Any:
        """Emit real tool_call (before) + tool_result (after, ok/duration)."""
        name = request.tool_call["name"]
        args = request.tool_call["args"]
        t0 = time.perf_counter()

        if self._tool_calls >= MAX_TOOL_CALLS:
            # Budget exhausted — do NOT execute. Honest error ToolMessage
            # (an observation for the model, not a retry).
            await self._emit_async(
                {
                    "event": "tool_result",
                    "tool": name,
                    "ok": False,
                    "error": "budget_exhausted",
                    "duration_ms": 0,
                }
            )
            return ToolMessage(
                content=self._budget_warning,
                name=name,
                tool_call_id=request.tool_call["id"],
                status="error",
            )

        self._tool_calls += 1
        await self._emit_async(
            {
                "event": "tool_call",
                "tool": name,
                "args": args,
                "turn": self._turns,
            }
        )
        try:
            result = await handler(request)
        except Exception as e:  # noqa: BLE001 — tool failure is an observation
            await self._emit_async(
                {
                    "event": "tool_result",
                    "tool": name,
                    "ok": False,
                    "error": str(e) or type(e).__name__,
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                }
            )
            # Convert the failure into an error ToolMessage so the model can
            # decide its next step (feeding an observation, not a retry).
            # Raising here would bubble through ToolNode's own handler which
            # re-raises by default (create_agent hard-fails the whole run on
            # tool exceptions — the wrong semantic for tool-level failures).
            return ToolMessage(
                content=f"工具执行失败: {e}",
                name=name,
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        ok = bool(getattr(result, "status", "success") != "error")
        await self._emit_async(
            {
                "event": "tool_result",
                "tool": name,
                "ok": ok,
                "duration_ms": int((time.perf_counter() - t0) * 1000),
                "summary": _emitted_summary(getattr(result, "content", "")),
                "error": None if ok else str(getattr(result, "content", ""))[:200],
            }
        )
        return result


def _last_has_tool_calls(result: list[Any]) -> bool:
    for m in reversed(result):
        if isinstance(m, AIMessage):
            return bool(getattr(m, "tool_calls", None))
    return False
