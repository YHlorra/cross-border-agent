"""Selection agent assembly ( 1.6) — official create_agent wiring.

``build_selection_agent`` renders the SOP prompt (agent.md) + the prose
history context (D8) and returns a compiled ``create_agent`` runnable the
/run server handler streams through. Middleware converts the loop's model
calls and tool executions into the NDJSON wire dialect.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel

from ..prompts import load_selection_system_prompt
from .events_middleware import EventsMiddleware

_HISTORY_HEADER = "## 历史上下文（上一轮选品结果，追问的指代对象）"


def build_selection_agent(
    *,
    model: BaseChatModel,
    tools: list[Any],
    events_queue: Optional[asyncio.Queue] = None,
    history_context: str = "",
    middleware: Optional[list[AgentMiddleware]] = None,
    system_prompt: Optional[str] = None,
    name: str = "selection_agent",
) -> Any:
    """Compile the selection agent.

    Args:
        model: LangChain chat model (the aimux adapter in production).
        tools: the agent tool registry (build_agent_tools()).
        events_queue: optional asyncio.Queue receiving wire event dicts; when
            given, an EventsMiddleware is appended (if ``middleware`` is also
            given both run — event emission is additive).
        history_context: prose-rendered prior-run envelope (render_history_context
            output) injected into the system prompt per D8.
        middleware: extra middleware instances for the loop.
        system_prompt: override the default agent.md prompt (tests).
    """
    if system_prompt is None:
        system_prompt = load_selection_system_prompt()

    if history_context and history_context.strip():
        system_prompt = (
            f"{system_prompt}\n\n{_HISTORY_HEADER}\n{history_context.strip()}"
        )

    mw: list[AgentMiddleware] = list(middleware or [])
    if events_queue is not None:
        mw.append(EventsMiddleware(events_queue.put))

    return create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=mw,
        name=name,
    )


