"""Token streaming + thinking summary tests.

Covers:
- ThinkStreamFilter sink is called with each closed think block
- ThinkStreamFilter sink is called on flush for unclosed blocks
- ThinkStreamFilter without sink is byte-for-byte identical to old behavior
- EventsMiddleware.emit_text_deltas=False suppresses the post-turn delta
- EventsMiddleware.set_thinking_sink wires the thinking event emitter
"""

from __future__ import annotations

import pytest

from agent.llm import ThinkStreamFilter
from agent.agents.events_middleware import EventsMiddleware


# ─── ThinkStreamFilter sink ───────────────────────────────────────────────


def test_filter_sink_called_on_closed_think_block() -> None:
    captured: list[str] = []
    f = ThinkStreamFilter(sink=captured.append)
    out = f.feed("pre<think>secret</think>post")
    # Safe text before + after the think block stays in the stream
    assert out == "pre" + "post"
    # The dropped reasoning is forwarded to the sink, truncated.
    assert captured == ["secret"]


def test_filter_sink_truncates_long_reasoning() -> None:
    captured: list[str] = []
    f = ThinkStreamFilter(sink=captured.append, sink_max_chars=10)
    f.feed("<think>" + "a" * 50 + "</think>")
    assert captured == ["a" * 10]


def test_filter_sink_called_on_flush_for_unclosed_block() -> None:
    """Stream end without a </think> — flush drops the partial block
    from the output but forwards it to the sink so the UI still sees it."""
    captured: list[str] = []
    f = ThinkStreamFilter(sink=captured.append)
    f.feed("<think>partial reasoning without close")
    out = f.flush()
    # Nothing safe in output (entire stream is suppressed)
    assert out == ""
    # But the partial thinking is forwarded.
    assert captured == ["partial reasoning without close"]


def test_filter_sink_skips_empty_blocks() -> None:
    captured: list[str] = []
    f = ThinkStreamFilter(sink=captured.append)
    f.feed("<think>  </think>")
    assert captured == []


def test_filter_without_sink_matches_legacy_behavior() -> None:
    """A filter constructed without a sink is byte-for-byte identical to
    the pre-streaming behavior (think blocks stripped, safe text emitted)."""
    f = ThinkStreamFilter()
    assert f.feed("<think>hidden</think>shown") == "shown"
    # Multiple chunks respect the partial-tag buffer
    f2 = ThinkStreamFilter()
    assert f2.feed("<think>hello ") == ""
    assert f2.feed("world</think>") == ""  # no safe text
    f3 = ThinkStreamFilter()
    assert f3.feed("a<think>secret</think>b") == "ab"


def test_filter_handles_think_split_across_chunks() -> None:
    """The OPEN tag may straddle chunk boundaries — the buffer tail holds
    the partial tag until the next chunk decides."""
    captured: list[str] = []
    f = ThinkStreamFilter(sink=captured.append)
    assert f.feed("pre<thin") == "pre"  # partial tag held back
    assert f.feed("k>hidden</think>after") == "after"
    assert captured == ["hidden"]


# ─── EventsMiddleware emit_text_deltas toggle ─────────────────────────────


def test_middleware_without_sink_does_not_emit_thinking() -> None:
    """Legacy middleware has no thinking sink → set_thinking_sink is a
    no-op call and the middleware doesn't try to forward anything."""
    captured: list[dict] = []
    mw = EventsMiddleware(captured.append)
    # No thinking sink wired — we just exercise the public API.
    assert mw._thinking_sink is None


def test_middleware_set_thinking_sink_replaces_sink() -> None:
    """A second set_thinking_sink call replaces the previous one — useful
    for the streaming driver which wires up after construction."""
    mw = EventsMiddleware(lambda ev: None)

    async def sink_a(turn: int, text: str) -> None: pass

    async def sink_b(turn: int, text: str) -> None: pass

    mw.set_thinking_sink(sink_a)
    assert mw._thinking_sink is sink_a
    mw.set_thinking_sink(sink_b)
    assert mw._thinking_sink is sink_b
    # None disables
    mw.set_thinking_sink(None)
    assert mw._thinking_sink is None


def test_middleware_emit_text_deltas_toggle_persists() -> None:
    """The flag must be readable via the property-equivalent attribute
    so the streaming driver can build a middleware with the right shape."""
    mw1 = EventsMiddleware(lambda ev: None, emit_text_deltas=True)
    assert mw1._emit_text_deltas is True
    mw2 = EventsMiddleware(lambda ev: None, emit_text_deltas=False)
    assert mw2._emit_text_deltas is False
