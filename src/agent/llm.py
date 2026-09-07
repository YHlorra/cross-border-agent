"""Aimux-backed model adapter.

Each LangGraph node calls one of ``AimuxChatModel.structured`` /
``.stream``. There is no mock implementation; missing / invalid env vars
raise ``StartupConfigError`` at boot, which ``errors.to_wire`` converts to a
structured error event for the frontend.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from typing import Any, AsyncIterator

import aimux
from pydantic import BaseModel

from . import observability as obs


class StartupConfigError(RuntimeError):
    """Raised when required LLM_* env vars are missing at boot."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(f"Missing required env vars: {', '.join(missing)}")


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise StartupConfigError([name])
    return val


def _model_env_var(*, primary: bool) -> str:
    return "LLM_PRIMARY_MODEL" if primary else "LLM_CHEAP_MODEL"


def build_model_from_args(
    provider_name: str,
    api_key: str,
    model_id: str,
    base_url: str | None = None,
) -> aimux.Model:
    """Build an aimux model from explicit args — try native constructor first,
    then registry. Shared by boot-time env path and the config-page path."""
    ctor = getattr(aimux, provider_name, None)
    if callable(ctor):
        try:
            return ctor(api_key, model_id, base_url)
        except TypeError:
            return ctor(api_key, model_id)

    try:
        return aimux.provider(
            name=provider_name,
            api_key=api_key,
            model_id=model_id,
            base_url=base_url,
        )
    except aimux.NoSuchProviderError as e:
        raise StartupConfigError(
            [f"LLM_PRIMARY_PROVIDER (unknown aimux provider {provider_name!r})"]
        ) from e


def _build_model(provider_name: str, *, primary: bool) -> aimux.Model:
    """Build an aimux model from env vars."""
    api_key = _require("LLM_API_KEY")
    model_id = _require(_model_env_var(primary=primary))
    base_url = os.environ.get("LLM_BASE_URL") or None
    return build_model_from_args(provider_name, api_key, model_id, base_url)


def build_primary_model() -> aimux.Model:
    return _build_model(_require("LLM_PRIMARY_PROVIDER"), primary=True)


def build_cheap_model() -> aimux.Model:
    return _build_model(_require("LLM_CHEAP_PROVIDER"), primary=False)


class ThinkStreamFilter:
    """Stateful filter that strips ``<think>…</think>`` blocks from a stream.

    Reasoning models (MiniMax-M3) open their output with a think block; the
    wire must never carry hidden reasoning to the frontend (2026-08-28 browser
    test: full English reasoning leaked into the report view). Stream chunks
    may split the tags at arbitrary boundaries, so a partial tag at the buffer
    tail is held back until the next chunk decides whether it is a real tag.
    An unclosed think block suppresses everything after it.

    optional ``sink`` callback. The reasoning
    text the filter would otherwise throw away is forwarded to the sink
    (truncated to ``sink_max_chars`` per closed block) so the frontend can
    render a collapsed "思考" card. A filter with no sink is byte-for-byte
    identical to the pre-streaming behavior — never wire, never leak.
    """

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(
        self,
        *,
        sink: "Callable[[str], None] | None" = None,
        sink_max_chars: int = 200,
    ) -> None:
        self._suppressing = False
        self._buf = ""
        self._sink = sink
        self._sink_max_chars = sink_max_chars
        # Accumulates the in-flight think text of the CURRENT block; the
        # sink is called only when a block closes (and on flush, below).
        self._block = ""

    def feed(self, delta: str) -> str:
        """Consume one stream chunk; return the text safe to emit now."""
        self._buf += delta
        out: list[str] = []
        while True:
            tag = self._CLOSE if self._suppressing else self._OPEN
            idx = self._buf.find(tag)
            if idx != -1:
                if self._suppressing:
                    # Closing the current think block — flush the captured
                    # reasoning to the sink, truncated.
                    self._block += self._buf[:idx]
                    if self._sink is not None and self._block.strip():
                        self._sink(self._block.strip()[: self._sink_max_chars])
                    self._block = ""
                else:
                    # Opening a think block — emit any safe text up to it.
                    if idx > 0:
                        out.append(self._buf[:idx])
                self._buf = self._buf[idx + len(tag) :]
                self._suppressing = not self._suppressing
                continue
            # No full tag: emit (or drop) everything except a tail that could
            # still grow into one.
            keep = self._partial_tail(tag)
            emit_len = len(self._buf) - keep
            if emit_len > 0:
                if self._suppressing:
                    self._block += self._buf[:emit_len]
                else:
                    out.append(self._buf[:emit_len])
                self._buf = self._buf[emit_len:]
            return "".join(out)

    def flush(self) -> str:
        """Drain the buffer at end-of-stream. If a think block was never
        closed, drop it from the output but DO forward it to the sink so
        the UI still gets a partial "思考" — better than silent loss."""
        if self._suppressing and self._sink is not None and (self._block + self._buf).strip():
            self._sink((self._block + self._buf).strip()[: self._sink_max_chars])
        self._block = ""
        out, self._buf = self._buf, ""
        return out

    def _partial_tail(self, tag: str) -> int:
        """Longest proper prefix of ``tag`` that is a suffix of the buffer."""
        for k in range(min(len(tag) - 1, len(self._buf)), 0, -1):
            if self._buf.endswith(tag[:k]):
                return k
        return 0


def strip_think_blocks(text: str) -> str:
    """Non-streaming counterpart for already-complete texts."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.replace("</think>", "").replace("<think>", "")


def extract_json_payload(raw: str) -> str:
    """Extract the JSON object substring from a raw LLM text response.

    Reasoning models (e.g. MiniMax) wrap output in ``<think>`` blocks and
    sometimes prose; aimux 0.3.0's ``generate_object`` cannot parse those, so
    ``AimuxChatModel.structured`` calls ``generate_text`` and uses this pure
    function to locate the JSON object before schema validation.
    """
    text = raw.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = text.replace("</think>", "")
    start = text.find("{")
    if start == -1:
        raise ValueError(f"no JSON object found in model output: {text[:120]!r}")
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError(f"no JSON object found in model output: {text[:120]!r}")


class AimuxChatModel:
    """Uniform surface over aimux.Model for nodes."""

    def __init__(self, model: aimux.Model) -> None:
        self._model = model

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        temperature: float = 0.2,
        trace_meta: dict[str, Any] | None = None,
    ) -> BaseModel:
        # aimux.generate_object cannot parse think-block outputs (probe
        # evidence 2026-08-28: fails even for trivial prompts on MiniMax), so
        # we call generate_text and extract + validate the JSON ourselves.
        # aimux is synchronous — run it in a thread so it cannot block the
        # aiohttp event loop (blocking kills incremental event streaming).
        meta = dict(trace_meta or {})
        name = meta.pop("name", "llm.call")
        model_id = getattr(self._model, "model_id", None) or getattr(
            self._model, "model", None
        )
        prompt: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        opts: dict[str, Any] = {"temperature": temperature}
        with obs.generation(meta, name=name, model=model_id) as gen:
            result = await asyncio.to_thread(
                aimux.generate_text, self._model, prompt, opts
            )
            raw = result["text"] if isinstance(result, dict) and "text" in result else result
            if not isinstance(raw, str):
                raise TypeError(
                    f"Unsupported aimux.generate_text return: {type(raw).__name__}"
                )
            payload = extract_json_payload(raw)
            gen.update(output=payload[:500])
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"LLM structured output is not valid JSON after extraction: {e};"
                    f" payload head: {payload[:200]!r}"
                ) from e
            return schema.model_validate(obj)

    async def stream(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.4,
        trace_meta: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        meta = dict(trace_meta or {})
        name = meta.pop("name", "llm.stream")
        model_id = getattr(self._model, "model_id", None) or getattr(
            self._model, "model", None
        )
        prompt = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        opts: dict[str, Any] = {"temperature": temperature}
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()

        def produce() -> None:
            # Consume the synchronous aimux iterator on a worker thread and
            # hand each delta back to the event loop, so streaming stays
            # incremental instead of blocking the loop until completion.
            try:
                for part in aimux.stream_text(self._model, prompt, opts):
                    if isinstance(part, dict) and "TextDelta" in part:
                        td = part["TextDelta"]
                        if isinstance(td, dict):
                            delta = td.get("delta", "")
                        else:
                            delta = getattr(td, "delta", "")
                        if delta:
                            loop.call_soon_threadsafe(queue.put_nowait, delta)
            except BaseException as e:  # propagate producer failure faithfully
                loop.call_soon_threadsafe(queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=produce, daemon=True).start()
        with obs.generation(meta, name=name, model=model_id):
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item


def current_config_snapshot() -> dict[str, Any]:
    """Echo the effective config without the secret."""
    return {
        "primary_provider": os.environ.get("LLM_PRIMARY_PROVIDER"),
        "primary_model": os.environ.get("LLM_PRIMARY_MODEL"),
        "cheap_provider": os.environ.get("LLM_CHEAP_PROVIDER"),
        "cheap_model": os.environ.get("LLM_CHEAP_MODEL"),
        "base_url": os.environ.get("LLM_BASE_URL") or None,
        "api_key_set": bool(os.environ.get("LLM_API_KEY")),
    }