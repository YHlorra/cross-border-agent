"""Observability (Langfuse) tests — langfuse-observability change.

Contract under test (specs/observability):
1. Missing LANGFUSE_* keys → no-op client; AimuxChatModel.structured returns
   identical results; no exception ever escapes the observability layer.
2. Keys present (fake client injected) → generations recorded with
   name/model/provider metadata, grouped under a run_id trace.
3. Stream cancellation marks the generation cancelled instead of leaving it
   hanging.
4. trace_meta from business call sites (e.g. "score") passes through as the
   generation name.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import BaseModel

from agent import llm as llm_mod
from agent import observability as obs


class _Schema(BaseModel):
    a: int


class _FakeModel:
    model_id = "fake-model"


def _fake_generate_text_ok(model: Any, prompt: Any, opts: Any) -> dict[str, Any]:
    return {"text": json.dumps({"a": 1}), "usage": {"total_tokens": 7}}


# ---- fake langfuse client --------------------------------------------------

class _GenRec:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = dict(kwargs)
        self.ended = False
        self.status = "ok"

    def end(self, **kwargs: Any) -> None:
        self.ended = True
        self.status = str(kwargs.get("level", self.status))

    def update(self, **kwargs: Any) -> None:
        self.kwargs.update(kwargs)
        if "status" in kwargs:
            self.status = str(kwargs["status"])


class _TraceRec:
    def __init__(self, kwargs: dict[str, Any], sink: list[_GenRec]) -> None:
        self.kwargs = kwargs
        self._sink = sink

    def generation(self, **kwargs: Any) -> _GenRec:
        g = _GenRec(kwargs)
        self._sink.append(g)
        return g


class _RecordingClient:
    noop = False

    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = []
        self.generations: list[_GenRec] = []
        self.flushed = False

    def trace(self, **kwargs: Any) -> _TraceRec:
        self.traces.append(kwargs)
        return _TraceRec(kwargs, self.generations)

    def flush(self) -> None:
        self.flushed = True


@pytest.fixture()
def recording(monkeypatch: pytest.MonkeyPatch) -> _RecordingClient:
    """Force the observability layer onto a fake recording client."""
    client = _RecordingClient()
    monkeypatch.setattr(obs, "get_langfuse", lambda: client)
    return client


@pytest.fixture(autouse=True)
def _no_real_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.delenv(key, raising=False)


# ---- tests -----------------------------------------------------------------


def test_missing_keys_yields_noop_client() -> None:
    client = obs.get_langfuse()
    assert client.noop is True
    # every surface is safe to touch
    client.flush()
    trace = client.trace(id="r1", session_id="s1", name="run")
    gen = trace.generation(name="llm.call", model="m")
    gen.end()
    gen.update(status="cancelled")


async def test_structured_noop_keys_identical_result(monkeypatch) -> None:
    monkeypatch.setattr(llm_mod.aimux, "generate_text", _fake_generate_text_ok)
    model = llm_mod.AimuxChatModel(_FakeModel())
    out = await model.structured(
        system="s", user="u", schema=_Schema, trace_meta={"name": "score"}
    )
    assert out == _Schema(a=1)


async def test_structured_records_generation(
    recording: _RecordingClient, monkeypatch
) -> None:
    monkeypatch.setattr(llm_mod.aimux, "generate_text", _fake_generate_text_ok)
    model = llm_mod.AimuxChatModel(_FakeModel())
    await model.structured(
        system="s",
        user="u",
        schema=_Schema,
        trace_meta={"name": "score", "run_id": "run-1", "session_id": "sess-1"},
    )
    assert len(recording.generations) == 1
    gen = recording.generations[0]
    assert gen.kwargs["name"] == "score"
    assert gen.kwargs["model"] == "fake-model"
    assert gen.ended is True
    # generations are grouped under the run_id trace with session linkage
    assert recording.traces and recording.traces[0]["id"] == "run-1"
    assert recording.traces[0]["session_id"] == "sess-1"


async def test_stream_cancelled_marks_generation(
    recording: _RecordingClient, monkeypatch
) -> None:
    deltas = iter(["a", "b", "c"])

    def fake_stream_text(model: Any, prompt: Any, opts: Any):
        for d in deltas:
            yield {"TextDelta": {"delta": d}}
        yield {"TextDelta": {"delta": "never-consumed"}}

    monkeypatch.setattr(llm_mod.aimux, "stream_text", fake_stream_text)
    model = llm_mod.AimuxChatModel(_FakeModel())

    agen = model.stream(
        system="s", user="u", trace_meta={"name": "loop", "run_id": "run-2"}
    )
    first = await agen.__anext__()
    assert first == "a"
    await agen.aclose()  # client went away mid-stream

    assert len(recording.generations) == 1
    gen = recording.generations[0]
    assert gen.status == "cancelled"


async def test_trace_meta_names_generation(
    recording: _RecordingClient, monkeypatch
) -> None:
    monkeypatch.setattr(llm_mod.aimux, "generate_text", _fake_generate_text_ok)
    model = llm_mod.AimuxChatModel(_FakeModel())
    await model.structured(system="s", user="u", schema=_Schema)
    # no trace_meta → default name, still traced
    assert recording.generations[0].kwargs["name"] == "llm.call"


def test_flush_noop_safe() -> None:
    obs.flush()  # must not raise with keys missing
