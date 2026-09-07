"""Durable execution tests (backend logic, no DBOS runtime).

Covers:
- switch matrix: AGENT_APPROVAL=on requires AGENT_DURABLE=on
- _drive_and_persist throat (called directly — DBOS steps execute as plain
  functions outside a workflow): U+FFFD gate, sequence from 1, save_event
  rows, final capture, tee delivery
- approval storage (record / resolve / list) on SQLite

Full DBOS crash-resume is exercised by scripts/probe_dbos.py (enqueue →
taskkill /F → resume, RESUME_OK on 2026-09-06) — live wiring with real LLM
drivers stays gated behind AGENT_DURABLE=on + LLM_API_KEY (documented遗留).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent import durable
from agent.llm import StartupConfigError


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.delenv("AGENT_DURABLE", raising=False)
    monkeypatch.delenv("AGENT_APPROVAL", raising=False)
    # PG-only：DATABASE_URL 由 conftest 提供（测试库），不再删除
    monkeypatch.setattr(durable, "_db_path", lambda: str(tmp_path / "memory.db"))
    durable._registry.pop("test", None)
    durable._registry.pop("test_approval", None)
    durable._queues.pop("test-run", None)
    durable._loops.pop("test-run", None)
    yield
    durable._registry.pop("test", None)
    durable._registry.pop("test_approval", None)
    durable._queues.pop("test-run", None)
    durable._loops.pop("test-run", None)


def _fake_driver(events: list[dict]):
    def factory(**payload):
        async def gen():
            for ev in events:
                yield ev
        return gen()
    return factory


# ---- 开关矩阵------------------------------------------------------


def test_matrix_approval_requires_durable(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DURABLE", "off")
    monkeypatch.setenv("AGENT_APPROVAL", "on")
    assert durable.approval_enabled() is True
    assert durable.enabled() is False
    # build_app 启动矩阵:approval 无 durable → StartupConfigError
    import agent.server as srv
    with pytest.raises(StartupConfigError):
        srv.build_app()


def test_enabled_requires_both(monkeypatch) -> None:
    # PG-only：DATABASE_URL 恒在（conftest 提供），缺 URL 的
    # False 分支改由显式清空 env 验证。
    monkeypatch.setenv("AGENT_DURABLE", "on")
    monkeypatch.setenv("DATABASE_URL", "")
    assert durable.enabled() is False  # 无 DATABASE_URL
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/crossborder_test")
    assert durable.enabled() is True


# ---- 咽喉逻辑(直接调用 step 函数)--------------------------------------------


async def test_drive_persists_gate_and_tee(tmp_path) -> None:
    events = [
        {"event": "tool_call", "name": "search"},
        {"event": "\ufffd-corrupted"},
        {"event": "final", "decision": "go"},
    ]
    durable.register_run_kind(
        "test", driver_factory=_fake_driver(events),
        on_final=lambda **_: None,
    )
    q: asyncio.Queue = asyncio.Queue()
    durable._queues["test-run"] = q
    durable._loops["test-run"] = asyncio.get_running_loop()

    result = await durable._drive_and_persist(
        "test", "sess-t", "test-run",
        {"query": "q", "history_context": "", "session_id": "sess-t", "run_id": "test-run"},
    )
    assert result["failed"] is True  # U+FFFD 事件被替换为 error
    assert result["final_event"]["decision"] == "go"

    # tee:三条 wire(坏事件已替换为 error 形状)
    tee: list[dict] = []
    while not q.empty():
        tee.append(q.get_nowait())
    assert [e["event"] for e in tee] == ["tool_call", "error", "final"]
    assert tee[1]["code"] == "EncodingError"

    from agent.persistence import store
    rows = store.list_session_events(
        session_id="sess-t",
        db_path=durable._db_path(),
    )
    assert [r["sequence"] for r in rows] == [1, 2, 3]
    assert rows[1]["event_type"] == "error"
    assert rows[1]["answer_failed"] is True
    assert rows[2]["event_type"] == "final"


async def test_drive_no_queue_still_persists(tmp_path) -> None:
    """崩溃恢复场景:无消费者队列时驱动器照常落库。"""
    events = [{"event": "final", "decision": "go"}]
    durable.register_run_kind(
        "test", driver_factory=_fake_driver(events),
        on_final=lambda **_: None,
    )
    result = await durable._drive_and_persist(
        "test", "sess-t2", "test-run-2",
        {"session_id": "sess-t2", "run_id": "test-run-2"},
    )
    assert result["final_event"]["decision"] == "go"
    from agent.persistence import store
    rows = store.list_session_events(
        session_id="sess-t2",
        db_path=durable._db_path(),
    )
    assert len(rows) == 1


# ---- 审批存储(SQLite 路径)----------------------------------------------------


def test_approval_record_resolve_list(tmp_path) -> None:
    from agent.persistence import store
    db = str(tmp_path / "memory.db")

    store.record_approval(run_id="run-a", session_id="s-a", kind="listing", summary="...", db_path=db)
    store.record_approval(run_id="run-b", session_id="s-b", kind="listing", summary="...", db_path=db)
    pending = store.list_approvals(status="pending", db_path=db)
    assert {r["run_id"] for r in pending} == {"run-a", "run-b"}
    store.resolve_approval(run_id="run-a", decision="approve", db_path=db)
    assert store.list_approvals(status="pending", db_path=db) == [] or all(
        r["run_id"] != "run-a" for r in store.list_approvals(status="pending", db_path=db)
    )
    resolved = store.list_approvals(status="resolved", db_path=db)
    assert resolved[0]["run_id"] == "run-a" and resolved[0]["decision"] == "approve"
    # 重复挂起幂等
    store.record_approval(run_id="run-b", session_id="s-b", kind="listing", summary="...", db_path=db)
    all_rows = store.list_approvals(status="all")
    assert sum(1 for r in all_rows if r["run_id"] == "run-b") == 1


def test_approval_routes_registered() -> None:
    """审批端点已在 build_app 注册(POST /runs/{id}/approval, GET /runs/approvals)。"""
    import agent.server as srv

    app = srv.build_app()
    paths = {r.resource for r in app.router.routes() if hasattr(r, "resource")}
    assert any("/runs/{run_id}/approval" in str(p) for p in paths)
    assert any("/runs/approvals" in str(p) for p in paths)
