"""Loop persistence contract at the server throat (ex test_run_runtime_flag).

No network: the tests patch stream_run_loop / stream_run_listing_loop on the
agent.server module and pin what the /run and /listing/run throats must
persist when the loop's final event lands — the loop runtime has no save
node, so the server throat owns durability (the graph runtimes
with their own save nodes are gone).
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import struct
import threading

import pytest

import agent.server as srv


def _async_gen(events):
    """An async generator factory — matches the production runtime shapes."""

    async def _gen():
        for ev in events:
            yield ev

    return _gen


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """PG-only：写侧隔离由 conftest 的 _clean_app_tables
    autouse fixture 提供（每测试 TRUNCATE 应用表）——原 SQLite
    _DEFAULT_DB 重定向已随双驱动退役。"""



def _run_body(query="宠物用品", session_id="s1"):
    return {
        "query": query,
        "session_id": session_id,
        "historyRunId": None,
    }


def _listing_body(session_id="s1"):
    return {
        "candidate": {
            "product_id": "1688_004",
            "name_cn": "宠物自动喂食器",
            "name_en": "Pet Feeder",
            "source_price_cny": 88.0,
            "target_price_usd": 39.99,
            "price_gap_ratio": 2.1,
            "category": "pet_supplies",
        },
        "marketCode": "US",
        "brand": "",
        "competitorBrands": [],
        "session_id": session_id,
    }


async def _post_listing(monkeypatch, loop_events):
    """Patch the listing loop with an async-gen factory and POST /listing/run."""

    def _loop(**kw):
        return _async_gen(loop_events)()

    monkeypatch.setattr(srv, "stream_run_listing_loop", _loop)
    monkeypatch.setattr(srv, "agents_models", {"listing": object()})

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    app = web.Application()
    app.router.add_post("/listing/run", srv.listing_run)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/listing/run", json=_listing_body())
        return resp.status, await resp.text()


def test_listing_loop_final_saves_listing_run(monkeypatch) -> None:
    """the loop runtime has no save node; the throat must mirror
    the legacy save node's envelope when the loop's final kind=listing lands."""
    saved: list[dict] = []

    def _fake_save_listing_run(**kw):
        saved.append(kw)

    monkeypatch.setattr(srv, "save_listing_run", _fake_save_listing_run)
    loop_events = [
        {"event": "node_start", "node": "draft"},
        {"event": "turn_start", "turn": 1},
        {"event": "tool_call", "tool": "submit_draft", "args": {}, "turn": 1},
        {
            "event": "final",
            "kind": "listing",
            "listing": {"item_name": "Pet Feeder"},
            "issues": [],
            "hard_failed": False,
            "product_id": "1688_004",
            "market_code": "US",
        },
    ]
    status, _ = asyncio.run(_post_listing(monkeypatch, loop_events))
    assert status == 200
    assert len(saved) == 1, "loop final must persist exactly one listing_runs row"
    kw = saved[0]
    assert kw["product_id"] == "1688_004"
    assert kw["market_code"] == "US"
    assert kw["listing"]["listing"] == {"item_name": "Pet Feeder"}
    assert kw["listing"]["candidate"]["product_id"] == "1688_004"
    assert kw["run_id"], "must honor the server-generated run id (replay contract)"


def test_run_loop_final_persists_run_and_edges(monkeypatch) -> None:
    """the loop runtime's final envelope must reach save_run (the
    persist-node contract mirror) and materialize_edges_for_run at the throat."""
    saved_runs: list[dict] = []
    materialized: list[dict] = []

    def _fake_save_run(**kw):
        saved_runs.append(kw)

    def _fake_materialize(**kw):
        materialized.append(kw)

    monkeypatch.setattr(srv, "save_run", _fake_save_run)
    monkeypatch.setattr(srv, "materialize_edges_for_run", _fake_materialize)

    loop_events = [
        {"event": "turn_start", "turn": 1},
        {
            "event": "final",
            "decision": "go",
            "report": {"seed_keyword": "宠物用品", "market_summary": "…"},
            "candidates": [
                {
                    "product_id": "1688_004",
                    "total_score": 7.2,
                    "category": "pet_supplies",
                }
            ],
        },
    ]

    def _loop(**kw):
        return _async_gen(loop_events)()

    monkeypatch.setattr(srv, "stream_run_loop", _loop)
    monkeypatch.setattr(srv, "agents_models", {"selection": object()})

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    async def _post() -> int:
        # fresh app per call — an aiohttp Application binds to its event loop
        app = web.Application()
        app.router.add_post("/run", srv.run)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/run", json=_run_body())
            return resp.status

    assert asyncio.run(_post()) == 200

    assert len(saved_runs) == 1
    kw = saved_runs[0]
    assert kw["seed_keyword"] == "宠物用品"
    assert kw["decision"] == "go"
    assert kw["report"]["candidates"][0]["product_id"] == "1688_004"
    assert kw["run_id"], "must honor the server-generated run id"
    assert materialized and materialized[0]["keyword"] == "宠物用品"


# ── P1 cancel chain at the throat ───────────────────────────────────────────


def test_client_disconnect_midstream_aborts_run(monkeypatch, caplog) -> None:
    """Automated stand-in for the P1 manual test (this environment has no
    LLM key): a raw TCP client that RSTs mid-stream must, within seconds,
    (a) close the loop generator via the throat's aclosing, and (b) hit the
    disconnect branch log instead of writing an error frame. aiohttp 3.14
    surfaces the dead write as ClientConnectionResetError — a
    ConnectionResetError subclass, so the run() except branch owns it."""
    closed = threading.Event()

    async def _fake_loop(**kw):
        try:
            yield {"event": "turn_start", "turn": 1}
            # bounded heartbeat (400 × 50ms) — a write is where the reset
            # surfaces, and the bound keeps a failing test from hanging
            # runner.cleanup on an unstoppable handler
            for i in range(400):
                yield {"event": "turn_end", "turn": 1 + i}
                await asyncio.sleep(0.05)
        except (GeneratorExit, asyncio.CancelledError):
            # set ONLY on teardown — natural completion must not satisfy
            # the "generator was closed" assertion below
            closed.set()
            raise

    monkeypatch.setattr(srv, "stream_run_loop", _fake_loop)
    monkeypatch.setattr(srv, "agents_models", {"selection": object()})

    from aiohttp import web

    async def _scenario() -> None:
        app = web.Application()
        app.router.add_post("/run", srv.run)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            body = json.dumps(_run_body())
            req = (
                f"POST /run HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body.encode())}\r\n\r\n{body}"
            )
            writer.write(req.encode())
            await writer.drain()
            # consume the response headers, then the first NDJSON event —
            # the StreamResponse is chunked, so the body is framed as
            # <size>\r\n<payload>\r\n and the payload is one line behind
            # the chunk-size line
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if line in (b"\r\n", b"\n"):
                    break
            await asyncio.wait_for(reader.readline(), timeout=5)  # chunk size
            first = await asyncio.wait_for(reader.readline(), timeout=5)
            assert b'"event"' in first
            # SO_LINGER(0) → RST, the abrupt close a browser abort produces
            sock = writer.get_extra_info("socket")
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2)
            except Exception:  # noqa: BLE001 — RST close may raise, irrelevant
                pass
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 5.0
            while not closed.is_set():
                if loop.time() > deadline:
                    raise AssertionError(
                        "loop generator was not closed within 5s of client abort"
                    )
                await asyncio.sleep(0.05)
        finally:
            await runner.cleanup()

    caplog.set_level(logging.INFO, logger="agent.server")
    try:
        asyncio.run(_scenario())
    finally:
        closed.set()  # never leak the heartbeat loop if an assert fired early
    assert closed.is_set()
    assert "client disconnected; run aborted" in caplog.text
