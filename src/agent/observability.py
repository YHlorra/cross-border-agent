"""Langfuse observability — single choke point for LLM call tracing.

langfuse-observability. Missing / broken Langfuse config
degrades to a structurally identical no-op client: call sites never branch
and the agent's behavior is byte-for-byte unchanged (spec: observability /
"观测不可用时零行为变化"). With keys present, every LLM call becomes a
Langfuse generation grouped under a run_id trace (session_id attached) so
the console can replay full multi-turn sessions.

Trace objects are passed manually around asyncio / to_thread boundaries
(design D-risk); only the lightweight run meta (session_id / run_id) rides
a ContextVar set once per run handler — ``real_embed``-style worker threads
must capture it before spawning (see llm_langchain._astream).
"""
from __future__ import annotations

import asyncio
import contextvars
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

_LANGFUSE_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
_DEFAULT_HOST = "https://cloud.langfuse.com"

_run_meta: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "agent_run_meta", default={}
)


def set_run_meta(session_id: str, run_id: str) -> None:
    """Anchor subsequent LLM generations to this run (call once per handler)."""
    _run_meta.set({"session_id": session_id, "run_id": run_id})


def get_run_meta() -> dict[str, Any]:
    return _run_meta.get()


# ---- no-op client (missing keys / degraded) --------------------------------


class _NullGeneration:
    def end(self, **_: Any) -> None: ...

    def update(self, **_: Any) -> None: ...


class _NullTrace:
    def generation(self, **_: Any) -> _NullGeneration:
        return _NullGeneration()

    def update(self, **_: Any) -> None: ...


class _NullClient:
    noop = True

    def trace(self, **_: Any) -> _NullTrace:
        return _NullTrace()

    def flush(self) -> None: ...


# ---- real client ------------------------------------------------------------


class _RealGeneration:
    def __init__(self, gen: Any) -> None:
        self._gen = gen

    def end(self, **kwargs: Any) -> None:
        self._gen.end(**kwargs)

    def update(self, **kwargs: Any) -> None:
        self._gen.update(**kwargs)


class _RealTrace:
    def __init__(self, trace: Any) -> None:
        self._trace = trace

    def generation(self, **kwargs: Any) -> _RealGeneration:
        return _RealGeneration(self._trace.generation(**kwargs))

    def update(self, **_: Any) -> None: ...


class _RealClient:
    noop = False

    def __init__(self) -> None:
        from langfuse import Langfuse  # lazy: only paid path imports it

        self._lf = Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=os.environ.get("LANGFUSE_HOST") or _DEFAULT_HOST,
        )

    def trace(self, **kwargs: Any) -> _RealTrace:
        return _RealTrace(self._lf.trace(**kwargs))

    def flush(self) -> None:
        self._lf.flush()


_client: Any = None
_resolved = False


def reset_client() -> None:
    """Test hook: drop the cached client so env changes are re-evaluated."""
    global _client, _resolved
    _client = None
    _resolved = False


def get_langfuse() -> Any:
    global _client, _resolved
    if not _resolved:
        _resolved = True
        if all(os.environ.get(k) for k in _LANGFUSE_KEYS):
            try:
                _client = _RealClient()
            except Exception:  # noqa: BLE001 — degrade, never break the agent
                _client = _NullClient()
        else:
            _client = _NullClient()
    return _client


def flush() -> None:
    get_langfuse().flush()


@contextmanager
def generation(
    meta: dict[str, Any] | None = None,
    *,
    name: str = "llm.call",
    model: str | None = None,
    provider: str | None = None,
) -> Iterator[Any]:
    """One LLM call as a Langfuse generation under the run's trace.

    Yields the generation object so callers can attach output/usage before
    the normal-exit ``end``. Cancellation (GeneratorExit / CancelledError)
    marks the generation ``cancelled``; other exceptions mark ``error`` —
    both re-raised unchanged.
    """
    merged = {**get_run_meta(), **(meta or {})}
    trace = get_langfuse().trace(
        id=merged.get("run_id") or None,
        session_id=merged.get("session_id"),
        name=merged.get("kind") or merged.get("run_id") or name,
    )
    gen = trace.generation(
        name=name,
        model=model,
        metadata={"provider": provider} if provider else None,
    )
    start = time.perf_counter()
    try:
        yield gen
    except (asyncio.CancelledError, GeneratorExit):
        gen.update(status="cancelled")
        raise
    except BaseException as exc:  # noqa: BLE001 — annotate then re-raise
        gen.update(status="error", status_message=type(exc).__name__)
        raise
    else:
        gen.end(latency_s=round(time.perf_counter() - start, 3))
