"""Durable run execution — durable-agent-tasks.

``AGENT_DURABLE=on`` (+ DATABASE_URL) moves the execution body of the three
run handlers into a DBOS workflow (single-executor tee, design D1):

  workflow step2   executes the registered driver for ``kind``, mirroring the
                   handler throat (U+FFFD gate → save_event → sequence), and
                   tees every wire event into a process-local queue.
  HTTP handler     consumes the queue and streams NDJSON to the client.
  crash            queue dies with the process; DBOS recovers the workflow on
                   the next launch and re-runs step2 (events dedup via
                   UNIQUE(session_id, run_id, sequence)); results surface
                   through the existing session-events replay endpoint.

Approval (``AGENT_APPROVAL=on``, requires durable on): the
listing workflow pauses before final persistence, emits ``approval_required``,
and waits on a durable message at the WORKFLOW layer (never inside a step —
a crash during the wait must recover from the wait, not re-run the driver).
Resolution arrives via ``POST /runs/{run_id}/approval`` → ``resolve_approval``
→ ``DBOS.send``.

``AGENT_DURABLE`` defaults to off: handlers keep the legacy path verbatim and
this module never configures/launches DBOS.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator, Callable

from dbos import DBOS

log = logging.getLogger("agent.durable")

APPROVAL_TIMEOUT_SECONDS = int(os.environ.get("AGENT_APPROVAL_TIMEOUT", "86400"))

_registry: dict[str, dict[str, Any]] = {}
_queues: dict[str, asyncio.Queue] = {}
_loops: dict[str, asyncio.AbstractEventLoop] = {}
_launched = False


def _db_path():
    """AGENT_DATA_DIR 设置时返回显式路径(测试/隔离);否则 None(默认库)。"""
    d = os.environ.get("AGENT_DATA_DIR")
    return os.path.join(d, "memory.db") if d else None

def enabled() -> bool:
    return os.environ.get("AGENT_DURABLE") == "on" and bool(
        os.environ.get("DATABASE_URL")
    )


def approval_enabled() -> bool:
    return os.environ.get("AGENT_APPROVAL") == "on"


def ensure_launched() -> None:
    """Idempotent DBOS configure+launch (server startup calls this)."""
    global _launched
    if not enabled() or _launched:
        return
    url = os.environ["DATABASE_URL"]
    DBOS(config={"name": "crossborder", "system_database_url": url, "app_database_url": url})
    DBOS.launch()
    _launched = True
    log.info("durable execution launched (DBOS)")


def register_run_kind(
    kind: str,
    *,
    driver_factory: Callable[..., AsyncIterator[dict]],
    on_final: Callable[..., Any],
    approval: bool = False,
) -> None:
    """Register the per-kind execution surface (JSON-safe payload contract)."""
    _registry[kind] = {
        "driver_factory": driver_factory,
        "on_final": on_final,
        "approval": approval and approval_enabled(),
    }


def abort(run_id: str) -> None:
    """Client went away / explicit stop: cancel the durable workflow (if any)
    and drop the tee queue. No-op when not durable."""
    _queues.pop(run_id, None)
    if _launched:
        try:
            DBOS.cancel_workflow(run_id)
        except Exception:  # noqa: BLE001 — best-effort, mirrors off-path
            log.exception("durable cancel failed for %s", run_id)


# ---- workflow body (registered once, dispatched by kind) --------------------


@DBOS.step()
async def _drive_and_persist(
    kind: str, session_id: str, run_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """STEP — the single execution body (design D1/D3).

    Mirrors the handler throat: sequence starts at 1, U+FFFD gate swaps the
    event for a structured EncodingError (answer_failed=1), every event is
    persisted, final/error flags tracked. Tees wire events to the process
    queue (lost on crash — recovery re-persists via idempotent save_event).
    """
    from .persistence import store

    entry = _registry[kind]
    q = _queues.get(run_id)
    sequence = 1
    failed = False
    final_event: dict[str, Any] | None = None
    driver = entry["driver_factory"](**payload)
    while True:
        try:
            event = await driver.__anext__()
        except StopAsyncIteration:
            break
        line = json.dumps(event, ensure_ascii=False, default=str)
        ev_type = event.get("event", "unknown")
        if "\ufffd" in line:
            failed = True
            bad_event = {
                "event": "error",
                "code": "EncodingError",
                "message": (
                    f"output contains U+FFFD replacement char at sequence "
                    f"{sequence} (provider upstream lossy decode); original "
                    f"event dropped"
                ),
                "session_id": session_id,
                "run_id": run_id,
            }
            store.save_event(
                session_id=session_id, run_id=run_id, event_type="error",
                sequence=sequence, payload=bad_event, answer_failed=failed,
                db_path=_db_path(),
            )
            sequence += 1
            if q is not None:
                _tee(run_id, bad_event)
            continue
        if ev_type == "error":
            failed = True
        if ev_type == "final":
            final_event = event
        try:
            store.save_event(
                session_id=session_id, run_id=run_id, event_type=ev_type,
                sequence=sequence, payload=event, answer_failed=failed,
                db_path=_db_path(),
            )
        except Exception:  # noqa: BLE001 — persistence stays best-effort
            log.exception("durable save_event failed (non-fatal)")
        sequence += 1
        if q is not None:
            _tee(run_id, event)
    return {"final_event": final_event, "failed": failed}


async def _finalize(
    kind: str,
    session_id: str,
    run_id: str,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> None:
    entry = _registry[kind]
    try:
        await entry["on_final"](
            session_id=session_id,
            run_id=run_id,
            payload=payload,
            final_event=result.get("final_event"),
            failed=bool(result.get("failed")),
        )
    except Exception:  # noqa: BLE001
        log.exception("durable on_final failed (non-fatal)")


@DBOS.workflow()
async def run_workflow(
    kind: str, session_id: str, run_id: str, payload: dict[str, Any]
) -> str:
    """WORKFLOW — durable recovery unit. The approval wait happens HERE
    (workflow layer), never inside a step: a crash during the wait recovers
    from the durable wait instead of re-running the driver."""
    from .persistence import store

    entry = _registry[kind]
    result = await _drive_and_persist(kind, session_id, run_id, payload)
    decision = "approved"
    if entry.get("approval") and result.get("final_event") is not None:
        summary = json.dumps(result["final_event"], ensure_ascii=False, default=str)[:500]
        store.record_approval(run_id=run_id, session_id=session_id, kind=kind, summary=summary, db_path=_db_path())
        _tee(run_id, {
            "event": "approval_required",
            "run_id": run_id,
            "kind": kind,
            "summary": summary,
        })
        decision = await DBOS.recv_async(
            "approval:" + run_id, timeout_seconds=APPROVAL_TIMEOUT_SECONDS
        )
        decision = decision if isinstance(decision, str) else "timeout"
        store.resolve_approval(
            run_id=run_id,
            decision=decision,
            reason=None if decision == "approve" else decision,
            db_path=_db_path(),
        )
        _tee(run_id, {
            "event": "approval_resolved",
            "run_id": run_id,
            "decision": decision,
        })
        if decision != "approve":
            await _finalize(kind, session_id, run_id, payload, {
                "final_event": None,
                "failed": True,
            })
            await _queues_put(run_id, None)
            return "cancelled:" + decision
    await _finalize(kind, session_id, run_id, payload, result)
    await _queues_put(run_id, None)  # sentinel: stream_events 消费完即退出
    return decision



def launch_run(
    kind: str, session_id: str, run_id: str, payload: dict[str, Any]
) -> None:
    ensure_launched()
    _queues[run_id] = asyncio.Queue()
    # DBOS 在自己的事件循环线程上执行 async workflow——tee 投递必须回到
    # handler 的循环(call_soon_threadsafe),否则 get 永远不会被唤醒。
    _loops[run_id] = asyncio.get_running_loop()
    DBOS.start_workflow(run_workflow, kind, session_id, run_id, payload)


def _tee(run_id: str, event: dict[str, Any]) -> None:
    """线程安全投递:workflow 线程 → handler 循环(队列已移除则丢弃)。"""
    q = _queues.get(run_id)
    loop = _loops.get(run_id)
    if q is None or loop is None:
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        q.put_nowait(event)
    else:
        loop.call_soon_threadsafe(q.put_nowait, event)


def resolve_approval(run_id: str, decision: str) -> None:
    DBOS.send(run_id, decision, "approval:" + run_id)


async def stream_events(run_id: str) -> AsyncIterator[dict[str, Any]]:
    """Consume the tee queue until the run finishes or the client aborts."""
    q = _queues.get(run_id)
    if q is None:
        raise RuntimeError(f"no durable stream for {run_id}")
    while True:
        event = await q.get()
        if event is None:
            break
        yield event
    _queues.pop(run_id, None)
    _loops.pop(run_id, None)
