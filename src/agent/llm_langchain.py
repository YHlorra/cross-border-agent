# llm_langchain.py — aimux → langchain BaseChatModel 适配器（降级预案的主战场）

"""Aimux-backed LangChain chat model adapter.

The official ``langchain.agents.create_agent``
loop drives a ``BaseChatModel``; this adapter is the ONLY hand-written layer
between the aimux gateway and the LangChain agent runtime (design.md D2).

Mapping summary:
- ``bind_tools`` stores the bound tools as aimux ``FunctionTool`` definitions
  and returns a bound copy of the adapter.
- ``_generate`` (create_agent's execute path) aggregates a streamed aimux
  call into one message: TextDeltas pass through ``ThinkStreamFilter``
  (reasoning think blocks never reach the wire), ToolCall parts map into
  ``AIMessage.tool_calls``. The
  transport is streamed even though the contract is non-streaming: provider
  gateways cut non-streamed long generations (~90s) that a listing draft
  routinely exceeds.
- ``_stream``/``_astream`` map ``aimux.wrapper.stream_text`` parts into
  ``ChatGenerationChunk``s (same think filter; tool-call parts buffered into
  a single ``tool_call_chunks`` chunk so create_agent can rebuild full tool
  calls from a stream). ``_stream`` is a plain sync generator per the
  ``BaseChatModel`` contract; ``_astream`` wraps it with the produce-thread
  pattern (llm.py) so the event loop never blocks.
- ``_convert_messages`` serializes assistant tool-call turns as typed
  ``tool_call`` content parts (AI-SDK shape) — never as raw JSON inside
  ``content``, which taught models to imitate the JSON as final answers.

The legacy ``AimuxChatModel`` (``llm.py``) is NOT deleted — it stays the
aimux gateway's structured-output surface (``model.structured`` powers the
score_candidates tool) and the selection runtime's model type.
"""
from __future__ import annotations

import json
import threading
from typing import Any, AsyncIterator, Iterator, Optional, Sequence, Union

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool
from pydantic import Field

from . import observability as obs
from .llm import AimuxChatModel, ThinkStreamFilter

try:  # optional: the typed aimux wrapper is always installed with aimux
    from aimux.wrapper import (
        FunctionTool,
        GenerateTextOptions,
        TimeoutConfiguration,
        generate_text as aimux_generate_text,
        stream_text as aimux_stream_text,
    )
except ImportError:  # pragma: no cover - defensive
    aimux_generate_text = None  # type: ignore[assignment]
    aimux_stream_text = None  # type: ignore[assignment]
    FunctionTool = None  # type: ignore[assignment,misc]
    GenerateTextOptions = None  # type: ignore[assignment,misc]
    TimeoutConfiguration = None  # type: ignore[assignment,misc]

__all__ = ["AimuxLangchainAdapter"]


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read an attribute-or-dict key from wrapper results / canned dicts."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _tool_to_function_tool(tool: Union[BaseTool, dict, Any]) -> Any:
    """Convert a LangChain tool (or dict) into an aimux ``FunctionTool``.

    ``BaseTool`` carries its JSON schema on ``args_schema``; plain dicts are
    passed through for tests that pre-bake aimux-shaped definitions.
    """
    if FunctionTool is None:  # pragma: no cover - defensive
        raise RuntimeError("aimux wrapper unavailable")
    if isinstance(tool, dict):
        return FunctionTool.model_validate(tool)
    name = getattr(tool, "name", None) or getattr(tool, "func", None).__name__
    description = getattr(tool, "description", None)
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None:
        schema = args_schema.model_json_schema()
    else:
        schema = {"type": "object", "properties": {}}
    return FunctionTool(
        name=name,
        description=description,
        input_schema=schema,
    )


class AimuxLangchainAdapter(BaseChatModel):
    """A ``BaseChatModel`` backed by an aimux ``Model``.

    Unlike the graph-runtime ``AimuxChatModel`` (llm.py), this adapter
    implements the two capabilities the LangChain agent runtime needs:
    ``bind_tools`` and tool-call parsing inside ``_generate``/``_stream``.
    """

    aimux_chat_model: Any = Field(exclude=True)  # the legacy wrapper (sync calls)
    tools: list[Any] = Field(default_factory=list, exclude=True)

    @property
    def _llm_type(self) -> str:
        return "aimux-langchain"

    def __init__(self, model: Any, **kwargs: Any) -> None:
        # Keep the sync call surface of llm.py's wrapper (structured / stream)
        # for the parts that reuse it (e.g. graph runtime callers).
        if isinstance(model, AimuxChatModel):
            wrapper = model
        else:
            wrapper = AimuxChatModel(model)
        super().__init__(aimux_chat_model=wrapper, **kwargs)

    def bind_tools(
        self,
        tools: Sequence[Union[BaseTool, dict, Any]],
        **kwargs: Any,
    ) -> "AimuxLangchainAdapter":
        """Store the tools as aimux FunctionTool definitions on a bound copy.

        The agent loop calls bind_tools once before each model call with the
        current tool list; storing on a copy keeps the base model reusable
        across calls with different tool sets.
        """
        bound = self.model_copy(deep=False)
        bound.tools = [_tool_to_function_tool(t) for t in tools]
        return bound

    def _build_options(self, stop: Optional[list[str]] = None, **kwargs: Any) -> Any:
        """Build typed aimux GenerateTextOptions for the current request."""
        if GenerateTextOptions is None:  # pragma: no cover - defensive
            raise RuntimeError("aimux wrapper unavailable")
        opts = GenerateTextOptions(temperature=kwargs.get("temperature", 0.4))
        # Long generations (a full listing draft via the non-streaming
        # create_agent path takes ~2-3 min with reasoning models) blow the
        # provider's default total timeout — the legacy graph survived only
        # because it streams. Give the whole call an explicit generous budget.
        if TimeoutConfiguration is not None:
            opts.timeout = TimeoutConfiguration(
                total_ms=300_000, first_chunk_ms=120_000, chunk_ms=120_000
            )
        if self.tools:
            opts.tools = self.tools
        if stop:
            opts.stop_sequences = stop
        return opts

    def _convert_messages(self, messages: list[BaseMessage]) -> list[dict[str, Any]]:
        """Serialize LangChain messages to aimux ModelMessage dicts.

        The aimux wire requires the OpenAI role vocabulary (system/user/
        assistant/tool; LangChain uses ``human`` — normalized here).
        Assistant tool-call turns use the wrapper's typed ``tool_call``
        content parts (AI-SDK shape) — the provider maps them to its native
        assistant.tool_calls field. Serializing the calls as raw JSON inside
        ``content`` (the legacy shape) taught the model a junk dialect: it
        started imitating ``[{"name": …, "args": …}]`` as its final answer
        text. ToolMessages become ``tool`` messages carrying a typed
        ``tool_result`` part (``is_error`` carries the error observation).
        """
        out: list[dict[str, Any]] = []
        for m in messages:
            content = m.content if isinstance(m.content, str) else ""
            if m.type == "tool":
                part: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_call_id": getattr(m, "tool_call_id", "") or "",
                    "result": content,
                }
                if getattr(m, "name", None):
                    part["tool_name"] = m.name
                if getattr(m, "status", "success") == "error":
                    part["is_error"] = True
                out.append({"role": "tool", "content": [part]})
                continue
            if isinstance(m, AIMessage):
                role = "assistant"
            elif isinstance(m, HumanMessage):
                role = "user"
            else:
                role = "system" if m.type == "system" else m.type
            if isinstance(m, AIMessage) and m.tool_calls:
                parts: list[dict[str, Any]] = []
                if content:
                    parts.append({"type": "text", "text": content})
                for tc in m.tool_calls:
                    parts.append(
                        {
                            "type": "tool_call",
                            "tool_call_id": tc.get("id", "") or "",
                            "tool_name": tc["name"],
                            "input": tc["args"],
                        }
                    )
                out.append({"role": role, "content": parts})
            else:
                out.append({"role": role, "content": content})
        return out

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Non-streaming contract for create_agent, streamed transport for the
        provider.

        Reasoning models' long generations (a full listing draft runs 2-3
        minutes) are killed by the provider gateway's ~90s cutoff on the
        non-streamed endpoint — the legacy graph survived only because it
        streams (chunks keep the connection alive). So this aggregates
        ``aimux_stream_text`` parts into one AIMessage: TextDeltas pass
        through ``ThinkStreamFilter`` (think blocks never reach the agent
        history / agent_delta / the final envelope), ToolCall parts map into
        ``tool_calls`` (create_agent hands these to its ToolNode).

        Protocol quirk (MiniMax, intermittent on second turn): the final
        ``ToolCall`` StreamPart occasionally arrives with an empty
        ``tool_name``. The matching ``ToolInputStart`` part always carries
        the name keyed by ``id``, so we buffer it and fall back when the
        final ``ToolCall`` drops the field. Without this, LangChain's ToolNode
        rejects the empty-name tool_call_chunks and the run fails (upstream
        400 on the next turn, or hard validation error in create_agent).
        """
        prompt = self._convert_messages(messages)
        opts = self._build_options(stop=stop, **kwargs)

        with obs.generation({}, name="agent.loop", model="aimux") as gen:
            think = ThinkStreamFilter()
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            tool_names_by_id: dict[str, str] = {}
            for part in aimux_stream_text(self.aimux_chat_model._model, prompt, opts):
                tag = next(iter(part))
                if tag == "TextDelta":
                    safe = think.feed((part.get(tag) or {}).get("delta", ""))
                    if safe:
                        text_parts.append(safe)
                elif tag == "ToolInputStart":
                    tid = _get(part[tag], "id", "") or ""
                    tname = _get(part[tag], "tool_name", "") or ""
                    if tid and tname:
                        tool_names_by_id[tid] = tname
                elif tag == "ToolCall":
                    tc = part[tag]
                    tid = _get(tc, "tool_call_id", "") or ""
                    tname = _get(tc, "tool_name", "") or tool_names_by_id.get(tid, "")
                    tool_calls.append(
                        {
                            "name": tname,
                            "args": (
                                _get(tc, "input")
                                if isinstance(_get(tc, "input"), dict)
                                else {}
                            ),
                            "id": tid,
                            "type": "tool_call",
                        }
                    )
            msg = AIMessage(content="".join(text_parts), tool_calls=tool_calls)
            gen.update(output="".join(text_parts)[:500])
            return ChatResult(generations=[ChatGeneration(message=msg)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """Streaming generation — a SYNC generator, per the BaseChatModel
        contract (BaseChatModel.astream runs this iterator on a worker
        thread itself; _astream below wraps it the same way).

        Text deltas pass through a ``ThinkStreamFilter`` (reasoning think
        blocks never reach the wire) and are yielded immediately as
        ``ChatGenerationChunk(AIMessageChunk(content=...))``. Tool-call parts
        are buffered and re-emitted as one ``tool_call_chunks`` chunk after
        the stream ends, so create_agent can rebuild full tool calls from a
        stream. Producer failures raise — never yielded as values.

        Protocol quirk (MiniMax, intermittent on second turn): the final
        ``ToolCall`` StreamPart occasionally arrives with an empty
        ``tool_name``. The matching ``ToolInputStart`` always carries the name
        keyed by ``id``, so we capture it into ``tool_names_by_id`` and fall
        back to it when the final ``ToolCall`` drops the field. Without this,
        LangChain's ToolNode rejects the empty-name tool_call_chunks and the
        run fails (upstream 400 on the next turn, or hard validation error in
        create_agent).
        """
        prompt = self._convert_messages(messages)
        opts = self._build_options(stop=stop, **kwargs)

        think = ThinkStreamFilter()
        tool_chunks: list[dict[str, Any]] = []
        tool_names_by_id: dict[str, str] = {}

        for part in aimux_stream_text(self.aimux_chat_model._model, prompt, opts):
            tag = next(iter(part))
            if tag == "TextDelta":
                safe = think.feed((part.get(tag) or {}).get("delta", ""))
                if safe:
                    yield ChatGenerationChunk(message=AIMessageChunk(content=safe))
            elif tag == "ToolInputStart":
                # Capture tool_name keyed by id so we can recover from a
                # ``ToolCall`` part that arrives with empty tool_name
                # (observed intermittently on MiniMax second turns).
                tid = _get(part[tag], "id", "") or ""
                tname = _get(part[tag], "tool_name", "") or ""
                if tid and tname:
                    tool_names_by_id[tid] = tname
            elif tag == "ToolCall":
                tc = part[tag]
                tid = _get(tc, "tool_call_id", "") or ""
                tname = _get(tc, "tool_name", "") or tool_names_by_id.get(tid, "")
                tool_chunks.append(
                    {
                        "name": tname,
                        "args": (
                            json.dumps(_get(tc, "input"), ensure_ascii=False)
                            if isinstance(_get(tc, "input"), dict)
                            else str(_get(tc, "input", ""))
                        ),
                        "id": tid,
                        "index": len(tool_chunks),
                    }
                )

        # Re-emit the buffered tool calls as a single chunk so the agent
        # loop's ToolNode can consume it (create_agent accumulates
        # tool_call_chunks into a full AIMessage).
        if tool_chunks:
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", tool_call_chunks=tool_chunks)
            )

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Async streaming: iterate the sync ``_stream`` generator on a worker
        thread and hand chunks back through a threadsafe queue, so the event
        loop never blocks on the sync aimux iterator (llm.py's produce-thread
        pattern). Since ``_stream`` is a plain sync generator (BaseChatModel
        contract), ``next()`` iteration here is the correct consumption; its
        exceptions are transparently re-raised on the async side."""
        import asyncio

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()

        def _produce_thread() -> None:
            try:
                gen = self._stream(messages, stop=stop, **kwargs)
                while True:
                    try:
                        item = next(gen)
                    except StopIteration:
                        break
                    if item is not None:
                        loop.call_soon_threadsafe(queue.put_nowait, item)
            except BaseException as e:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=_produce_thread, daemon=True).start()
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
