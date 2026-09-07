"""SEAM — aimux→langchain message mapping (llm_langchain.py adapter).

No LLM, no network: canned aimux results feed the adapter's pure mapping
layers and assert the langchain message shapes create_agent consumes.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from agent.llm import ThinkStreamFilter
from agent.llm_langchain import AimuxLangchainAdapter, _tool_to_function_tool


# ── canned aimux result shapes (mirror aimux.wrapper return structs) ────────


def _canned_text_result(text: str) -> dict:
    return {
        "text": text,
        "tool_calls": [],
        "finish_reason": {"unified": "stop", "raw": "stop"},
        "usage": None,
        "warnings": [],
        "raw": None,
    }


def _canned_tool_result(tool_name: str, tool_call_id: str, input: dict) -> dict:
    return {
        "text": "",
        "tool_calls": [
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "input": input,
            }
        ],
        "finish_reason": {"unified": "tool-calls", "raw": "tool_calls"},
        "usage": None,
        "warnings": [],
        "raw": None,
    }


class _FakeAimuxModel:
    """Minimal aimux.Model stand-in: the adapter only touches ._model by
    passing it into aimux wrapper functions, which we patch in the tests."""

    def __init__(self, canned: dict) -> None:
        self.canned = canned


@pytest.fixture
def adapter_with(monkeypatch):
    """Build an AimuxLangchainAdapter whose aimux calls return canned results."""

    def _make(canned_result: dict, canned_stream_parts: list[dict] | None = None):
        from aimux.wrapper import FunctionTool as _FT

        model = _FakeAimuxModel(canned_result)
        adapter = AimuxLangchainAdapter(model)

        def fake_generate_text(m, prompt, options=None):
            return _CannedResultAdapter(canned_result)

        monkeypatch.setattr("agent.llm_langchain.aimux_generate_text", fake_generate_text)

        if canned_stream_parts is not None:
            def fake_stream_text(m, prompt, options=None):
                return iter(canned_stream_parts)

            monkeypatch.setattr("agent.llm_langchain.aimux_stream_text", fake_stream_text)
        return adapter

    return _make


class _CannedResultAdapter:
    """Wrap the canned dict so attribute access matches the wrapper result."""

    def __init__(self, data: dict) -> None:
        self.text = data.get("text", "")
        self.tool_calls = data.get("tool_calls", [])
        self.finish_reason = type("FR", (), {"unified": data.get("finish_reason", {}).get("unified", "stop")})()
        self.usage = data.get("usage")
        self.warnings = data.get("warnings", [])


def _fake_tool_definition() -> dict:
    return {
        "type": "function",
        "name": "search_1688_products",
        "description": "Search 1688 products",
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string"}},
            "required": ["keyword"],
        },
    }


# ─── _tool_to_function_tool ─────────────────────────────────────────────────


def test_tool_dict_passthrough_to_function_tool() -> None:
    ft = _tool_to_function_tool(_fake_tool_definition())
    assert ft.name == "search_1688_products"
    assert ft.type == "function"
    assert ft.input_schema["required"] == ["keyword"]


def test_langchain_tool_converts_to_function_tool() -> None:
    from langchain_core.tools import tool

    @tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    ft = _tool_to_function_tool(add)
    assert ft.name == "add"
    assert "a" in ft.input_schema["properties"]
    assert ft.input_schema["required"] == ["a", "b"]


# ─── bind_tools keeps definitions on the bound copy ─────────────────────────


def test_bind_tools_returns_copy_with_definitions(adapter_with) -> None:
    adapter = adapter_with(_canned_text_result("hi"))
    bound = adapter.bind_tools([_fake_tool_definition()])
    assert bound is not adapter
    assert len(bound.tools) == 1
    assert bound.tools[0].name == "search_1688_products"
    # original untouched
    assert adapter.tools == []


# ─── _generate maps canned results to langchain messages ────────────────────


def test_generate_text_only_maps_to_aimessage(adapter_with) -> None:
    from langchain_core.messages import HumanMessage

    adapter = adapter_with(
        _canned_text_result(""),
        canned_stream_parts=[
            {"TextDelta": {"id": "1", "delta": "## 市场概览\n"}},
            {"TextDelta": {"id": "2", "delta": "耳机市场…"}},
        ],
    )
    result = adapter._generate([HumanMessage(content="查耳机")])
    msg = result.generations[0].message
    assert type(msg).__name__ == "AIMessage"
    assert msg.content == "## 市场概览\n耳机市场…"
    assert msg.tool_calls == []


def test_generate_strips_think_blocks(adapter_with) -> None:
    """create_agent drives _generate — reasoning think blocks must not leak
    into the agent history / agent_delta / final envelope. Closed blocks are
    removed; an unclosed one suppresses the rest (ThinkStreamFilter
    semantics, applied to the aggregated stream)."""
    from langchain_core.messages import HumanMessage

    closed = adapter_with(
        _canned_text_result(""),
        canned_stream_parts=[
            {"TextDelta": {"id": "1", "delta": "<think>推理…"}},
            {"TextDelta": {"id": "2", "delta": "</think>\n## 市场概览"}},
        ],
    )
    msg = closed._generate([HumanMessage(content="q")]).generations[0].message
    assert msg.content == "\n## 市场概览"

    unclosed = adapter_with(
        _canned_text_result(""),
        canned_stream_parts=[
            {"TextDelta": {"id": "1", "delta": "## 市场概览\n"}},
            {"TextDelta": {"id": "2", "delta": "<think>没写完的推理"}},
        ],
    )
    msg2 = unclosed._generate([HumanMessage(content="q")]).generations[0].message
    assert msg2.content == "## 市场概览\n"


def test_generate_tool_calls_maps_to_aimessage_tool_calls(adapter_with) -> None:
    from langchain_core.messages import HumanMessage

    adapter = adapter_with(
        _canned_tool_result("search_1688_products", "call_1", {"keyword": "耳机"}),
        canned_stream_parts=[
            {
                "ToolCall": {
                    "tool_call_id": "call_1",
                    "tool_name": "search_1688_products",
                    "input": {"keyword": "耳机"},
                }
            }
        ],
    )
    result = adapter._generate([HumanMessage(content="查耳机")])
    msg = result.generations[0].message
    assert msg.tool_calls, "expected tool_calls on the AIMessage"
    tc = msg.tool_calls[0]
    assert tc["name"] == "search_1688_products"
    assert tc["id"] == "call_1"
    assert tc["args"] == {"keyword": "耳机"}


# ─── _stream maps canned stream parts to chunks (think-filtered) ────────────


def _collect_chunks(adapter, messages) -> list[Any]:
    """Consume the adapter's sync stream generator to completion (_stream
    is a plain sync generator per the BaseChatModel contract)."""
    return list(adapter._stream(messages))


def _collect_achunks(adapter, messages) -> list[Any]:
    """Consume the adapter's async stream to completion."""
    import asyncio

    async def _run() -> list[Any]:
        return [c async for c in adapter._astream(messages)]

    return asyncio.run(_run())


def test_stream_text_deltas_filter_think_blocks(adapter_with) -> None:
    from langchain_core.messages import HumanMessage

    adapter = adapter_with(
        _canned_text_result(""),
        canned_stream_parts=[
            {"TextDelta": {"id": "1", "delta": "思考过程 <think>"}},
            {"TextDelta": {"id": "2", "delta": "内部推理</think> 公开内容"}},
        ],
    )
    chunks = _collect_chunks(adapter, [HumanMessage(content="x")])
    text = "".join(c.message.content for c in chunks if c.message.content)
    assert "思考过程" in text
    assert "内部推理" not in text
    assert "公开内容" in text


def test_stream_text_toolcall_parts_accumulate_into_tool_chunk(adapter_with) -> None:
    from langchain_core.messages import HumanMessage

    adapter = adapter_with(
        _canned_text_result(""),
        canned_stream_parts=[
            {"ToolInputStart": {"id": "1", "tool_name": "search_1688_products"}},
            {"ToolInputDelta": {"id": "1", "delta": '{"keyword":'}},
            {"ToolInputDelta": {"id": "1", "delta": ' "耳机"}'}},
            {
                "ToolCall": {
                    "tool_call_id": "call_1",
                    "tool_name": "search_1688_products",
                    "input": {"keyword": "耳机"},
                }
            },
        ],
    )
    chunks = _collect_chunks(adapter, [HumanMessage(content="x")])
    tool_chunks = [c for c in chunks if getattr(c.message, "tool_call_chunks", None)]
    assert len(tool_chunks) == 1
    tcc = tool_chunks[0].message.tool_call_chunks[0]
    assert tcc["name"] == "search_1688_products"
    assert tcc["id"] == "call_1"
    assert json.loads(tcc["args"]) == {"keyword": "耳机"}


def test_stream_empty_no_chunks(adapter_with) -> None:
    from langchain_core.messages import HumanMessage

    adapter = adapter_with(_canned_text_result(""), canned_stream_parts=[])
    chunks = _collect_chunks(adapter, [HumanMessage(content="x")])
    assert chunks == []


# ─── MiniMax second-turn tool_name recovery (hunt regression) ──────────────
#
# Background (hunt 2026-09-06): on the second agent turn (continuation after
# tool result), the MiniMax provider occasionally emits the final ``ToolCall``
# StreamPart with an empty ``tool_name``. Without the ToolInputStart → name
# buffer, _stream forwards ``name=""`` to langchain's tool_call_chunks and
# the ToolNode rejects the call (upstream 400 / hard validation error on
# create_agent). The adapter now buffers tool_name by id from ToolInputStart
# and falls back to it when the final ToolCall drops the field.


def test_stream_recovers_empty_tool_name_via_tool_input_start(adapter_with) -> None:
    """MiniMax protocol quirk: final ``ToolCall`` part may arrive with empty
    ``tool_name`` while the matching ``ToolInputStart`` carries it. The
    adapter must recover the name from the start-of-stream buffer keyed by
    ``id`` and emit a fully-formed tool_call_chunks entry."""
    from langchain_core.messages import HumanMessage

    adapter = adapter_with(
        _canned_text_result(""),
        canned_stream_parts=[
            {"ToolInputStart": {"id": "call_xyz", "tool_name": "search_1688_products"}},
            {"ToolInputDelta": {"id": "call_xyz", "delta": '{"keyword":'}},
            {"ToolInputDelta": {"id": "call_xyz", "delta": ' "耳机"}'}},
            {"ToolInputEnd": {"id": "call_xyz"}},
            # CRITICAL: tool_name is empty here on the final part — the bug.
            {
                "ToolCall": {
                    "tool_call_id": "call_xyz",
                    "tool_name": "",
                    "input": {"keyword": "耳机"},
                }
            },
        ],
    )
    chunks = _collect_chunks(adapter, [HumanMessage(content="x")])
    tool_chunks = [c for c in chunks if getattr(c.message, "tool_call_chunks", None)]
    assert len(tool_chunks) == 1, (
        f"expected one trailing tool_call_chunks chunk; got {len(tool_chunks)}"
    )
    tcc = tool_chunks[0].message.tool_call_chunks[0]
    assert tcc["name"] == "search_1688_products", (
        f"name must be recovered from ToolInputStart; got {tcc['name']!r}"
    )
    assert tcc["id"] == "call_xyz"
    assert json.loads(tcc["args"]) == {"keyword": "耳机"}


def test_stream_drops_tool_input_start_when_tool_call_has_name(adapter_with) -> None:
    """Happy path: ToolCall arrives with its own tool_name. The buffered
    ToolInputStart must not shadow or alter the final part's name (and
    must not leak a second tool call when the ids mismatch)."""
    from langchain_core.messages import HumanMessage

    adapter = adapter_with(
        _canned_text_result(""),
        canned_stream_parts=[
            {"ToolInputStart": {"id": "call_a", "tool_name": "WRONG_NAME"}},
            {"ToolInputDelta": {"id": "call_a", "delta": '{"keyword":"耳机"}'}},
            {
                "ToolCall": {
                    "tool_call_id": "call_a",
                    "tool_name": "search_1688_products",
                    "input": {"keyword": "耳机"},
                }
            },
        ],
    )
    chunks = _collect_chunks(adapter, [HumanMessage(content="x")])
    tool_chunks = [c for c in chunks if getattr(c.message, "tool_call_chunks", None)]
    assert len(tool_chunks) == 1
    assert tool_chunks[0].message.tool_call_chunks[0]["name"] == "search_1688_products"


def test_generate_recovers_empty_tool_name_via_tool_input_start(adapter_with) -> None:
    """Same recovery in the non-streaming ``_generate`` path (create_agent's
    execute path). The streamed transport still drives it (see _generate's
    docstring), and the same buffer logic must hold."""
    from langchain_core.messages import HumanMessage

    adapter = adapter_with(
        _canned_text_result(""),
        canned_stream_parts=[
            {"ToolInputStart": {"id": "call_q", "tool_name": "search_1688_products"}},
            {"ToolInputDelta": {"id": "call_q", "delta": '{"keyword":"耳机"}'}},
            {
                "ToolCall": {
                    "tool_call_id": "call_q",
                    "tool_name": "",
                    "input": {"keyword": "耳机"},
                }
            },
        ],
    )
    result = adapter._generate([HumanMessage(content="x")])
    msg = result.generations[0].message
    assert msg.tool_calls, "expected tool_calls on the AIMessage"
    assert msg.tool_calls[0]["name"] == "search_1688_products"
    assert msg.tool_calls[0]["id"] == "call_q"
    assert msg.tool_calls[0]["args"] == {"keyword": "耳机"}


def test_stream_missing_tool_input_start_yields_empty_name(adapter_with) -> None:
    """Defensive baseline: if ToolInputStart was never emitted (degenerate
    protocol — should not happen, but we don't want a NameError), the
    tool_call_chunks still carries an empty name. create_agent will reject
    it upstream, which is the correct failure mode (no silent name
    fabrication — recovery only kicks in when the buffer actually has it).
    """
    from langchain_core.messages import HumanMessage

    adapter = adapter_with(
        _canned_text_result(""),
        canned_stream_parts=[
            {
                "ToolCall": {
                    "tool_call_id": "call_unknown",
                    "tool_name": "",
                    "input": {"keyword": "耳机"},
                }
            },
        ],
    )
    chunks = _collect_chunks(adapter, [HumanMessage(content="x")])
    tool_chunks = [c for c in chunks if getattr(c.message, "tool_call_chunks", None)]
    assert len(tool_chunks) == 1
    # Recovery buffer has no entry for this id — name stays empty (we don't
    # fabricate names; that would mask a real protocol violation).
    assert tool_chunks[0].message.tool_call_chunks[0]["name"] == ""


# ─── _astream wraps the sync generator (produce-thread) ─────────────────────


def test_astream_chunk_sequence_matches_stream(adapter_with) -> None:
    """Same canned parts → _astream emits the identical chunk sequence as
    _stream (think filtering and the trailing tool_call_chunks chunk both
    carry over through the produce-thread wrapper)."""
    from langchain_core.messages import HumanMessage

    parts = [
        {"TextDelta": {"id": "1", "delta": "思考 <think>内部"}},
        {"TextDelta": {"id": "2", "delta": "推理</think> 公开"}},
        {"ToolInputStart": {"id": "1", "tool_name": "search_1688_products"}},
        {
            "ToolCall": {
                "tool_call_id": "call_1",
                "tool_name": "search_1688_products",
                "input": {"keyword": "耳机"},
            }
        },
    ]
    adapter = adapter_with(_canned_text_result(""), canned_stream_parts=parts)
    msgs = [HumanMessage(content="x")]
    sync_chunks = _collect_chunks(adapter, msgs)
    a_chunks = _collect_achunks(adapter, msgs)
    assert len(a_chunks) == len(sync_chunks) >= 2
    for a, s in zip(a_chunks, sync_chunks):
        assert a.message.content == s.message.content
        assert getattr(a.message, "tool_call_chunks", None) == getattr(
            s.message, "tool_call_chunks", None
        )


def test_stream_and_astream_propagate_producer_errors(adapter_with, monkeypatch) -> None:
    """A producer failure raises out of both hooks — never yielded as a
    value (the old ``yield e`` hack is gone with the contract fix)."""
    from langchain_core.messages import HumanMessage

    adapter = adapter_with(_canned_text_result(""), canned_stream_parts=[])

    def boom(m, prompt, options=None):
        def _gen():
            yield {"TextDelta": {"id": "1", "delta": "ok"}}
            raise RuntimeError("provider blew up")

        return _gen()

    monkeypatch.setattr("agent.llm_langchain.aimux_stream_text", boom)
    msgs = [HumanMessage(content="x")]
    with pytest.raises(RuntimeError, match="provider blew up"):
        _collect_chunks(adapter, msgs)
    with pytest.raises(RuntimeError, match="provider blew up"):
        _collect_achunks(adapter, msgs)
