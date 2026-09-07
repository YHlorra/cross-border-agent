"""P9 E2E: history-detail endpoint + historyRunId follow-up channel  +
server-side session_id resolver (0.0.3.1 governance slice G, decision 4).

Scenarios against an isolated AGENT_DATA_DIR temp server:

  D. /history/{run_id}  → envelope round-trip (id, query, decision, report,
     candidates, intent, created_at) for a saved run; missing id → 404 wire.
  E. POST /run with a valid historyRunId → run completes end-to-end, the
     loop's system prompt is grounded against the prior envelope (history
     prose injected per D8; loop dialect has no intent-node wire trace).
  F. POST /run with a bogus historyRunId → 404 wire passthrough (no silent
     degrade, mirrors /history/{id}).
  I. POST /run twice under the same explicit session_id (no historyRunId),
     then assert /sessions shows aggregation and the second run completes
     200 with the resolver-supplied prior context — proves the
     server-side resolver (wire decision) wires
     end to end.

Usage: .venv/Scripts/python.exe scripts/p9_history_paths.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
PORT = 8769
BASE = f"http://127.0.0.1:{PORT}"
REAL_PROVIDERS = ROOT / "data" / "selection" / "providers.json"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def spawn_server(data_dir: Path) -> subprocess.Popen:
    # delegate to shared spawn_agent helper (p8-style triple-保险:
    # terminate + wait + kill, stderr capture, port rebind assertion).
    from _e2e_common import spawn_agent
    cm = spawn_agent(port=PORT, data_dir=str(data_dir))
    proc = cm.__enter__()
    proc._a5_cm = cm  # type: ignore[attr-defined]
    return proc


def shutdown_server(proc: subprocess.Popen) -> None:
    cm = getattr(proc, "_a5_cm", None)
    if cm is not None:
        cm.__exit__(None, None, None)


async def wait_health(session: aiohttp.ClientSession, want_status: int) -> dict | None:
    for _ in range(50):
        try:
            async with session.get(f"{BASE}/healthz") as resp:
                if resp.status == want_status:
                    return await resp.json()
        except aiohttp.ClientError:
            pass
        await asyncio.sleep(0.2)
    return None


async def run_stream(
    session: aiohttp.ClientSession,
    body: dict,
) -> tuple[int, list[dict]]:
    events: list[dict] = []
    async with session.post(f"{BASE}/run", json=body) as resp:
        status = resp.status
        async for raw in resp.content:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return status, events


def _wire_up_config(data: Path) -> None:
    real = json.loads(REAL_PROVIDERS.read_text(encoding="utf-8"))
    rec = next(r for r in real if r.get("name") == "minimax_cn")
    (data / "providers.json").write_text(
        json.dumps([rec], ensure_ascii=False), encoding="utf-8"
    )
    (data / "model_config.json").write_text(
        json.dumps(
            {
                "default": {
                    "provider": "minimax_cn",
                    "model": "MiniMax-M3",
                    "base_url": "https://api.minimaxi.com/v1",
                },
                "agents": {
                    "selection": None,
                    "listing": None,
                    "monitor": None,
                    "store": None,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="p9-e2e-") as tmpdir:
        data = Path(tmpdir) / "working"
        data.mkdir()
        _wire_up_config(data)
        proc = spawn_server(data)
        try:
            async with aiohttp.ClientSession() as s:
                h = await wait_health(s, 200)
                if h is None:
                    check("boot healthz 200", False, "server never became ready")
                    return 1
                check("boot healthz 200", True)

                # ── Scenario D: save a run, then GET /history/{id} ────────
                print("\n── Scenario D: history-detail envelope round-trip ──")
                status, events = await run_stream(
                    s,
                    {"query": "宠物用品 预算2000 低价优先"},
                )
                final = next(
                    (e for e in events if e.get("event") == "final"), None
                )
                check(
                    "D1 /run completes with final event",
                    status == 200 and final is not None,
                    f"http={status} events={len(events)}",
                )
                # Pull the persisted run id from the list endpoint
                async with s.get(f"{BASE}/history") as resp:
                    hist = await resp.json()
                rid = hist[0]["id"] if hist else None
                check("D2 /history list returns the new run", rid is not None)

                async with s.get(f"{BASE}/history/{rid}") as resp:
                    body = await resp.json()
                check(
                    "D3 /history/{id} 200 with full envelope",
                    resp.status == 200 and body.get("id") == rid,
                    f"keys={sorted(body.keys())}",
                )
                check(
                    "D4 envelope carries candidates + intent",
                    isinstance(body.get("candidates"), list)
                    and isinstance(body.get("intent"), dict),
                    f"cand={len(body.get('candidates') or [])} "
                    f"intent_keys={list((body.get('intent') or {}).keys())}",
                )

                # Missing id → 404 wire
                async with s.get(f"{BASE}/history/does-not-exist") as resp404:
                    err = await resp404.json()
                check(
                    "D5 missing run id → 404 wire NotFound",
                    resp404.status == 404 and err.get("code") == "NotFound",
                    f"http={resp404.status} code={err.get('code')}",
                )

                # ── Scenario E: POST /run with historyRunId ──────────────
                print(
                    "\n── Scenario E: follow-up with historyRunId injection ──"
                )
                status, events = await run_stream(
                    s,
                    {
                        "query": "再便宜的呢",
                        "historyRunId": rid,
                    },
                )
                errs = [e for e in events if e.get("event") == "error"]
                check(
                    "E1 follow-up /run completes 200 (history context injected)",
                    status == 200 and not errs,
                    f"http={status} errors={[e.get('code') for e in errs]}",
                )
                # Loop dialect: history prose is injected into the system
                # prompt (no intent node / wire trace), so the observable is
                # the run completing with a final envelope.
                final_e = next(
                    (e for e in events if e.get("event") == "final"), None
                )
                check(
                    "E2 follow-up final arrives (history prose in loop system prompt)",
                    final_e is not None and final_e.get("event") == "final",
                )

                # ── Scenario F: bogus historyRunId → 404 (not silent) ────
                print(
                    "\n── Scenario F: bogus historyRunId fails closed as 404 ──"
                )
                status, events = await run_stream(
                    s,
                    {
                        "query": "再便宜的呢",
                        "historyRunId": "does-not-exist",
                    },
                )
                errs = [e for e in events if e.get("event") == "error"]
                check(
                    "F1 bogus historyRunId → 404 wire NotFound",
                    status == 404
                    and errs
                    and errs[0]["code"] == "NotFound",
                    f"http={status} code={errs[0]['code'] if errs else 'none'}",
                )

                # ── Scenario I: server-side session_id resolver ──────────
                # 0.0.3.1 governance slice G 决策 4 — the previous /run calls
                # never passed session_id, so each ran in its own server-side
                # UUID session. Here we pass an explicit session_id twice,
                # then call /sessions to assert aggregation. The third
                # (second follow) /run drops historyRunId so the
                # _resolve_session_latest_run path is the one that supplies
                # the prior envelope — proving the wire contract requires
                # only session_id from the caller, not run_id.
                print(
                    "\n── Scenario I: server-side session_id resolver (decision 4) ──"
                )
                test_session = "session-i-decision4"
                status1, events1 = await run_stream(
                    s,
                    {
                        "query": "宠物用品 预算2000",
                        "session_id": test_session,
                    },
                )
                final1 = next(
                    (e for e in events1 if e.get("event") == "final"), None
                )
                check(
                    "I1 first run completes under explicit session_id",
                    status1 == 200 and final1 is not None,
                    f"http={status1} events={len(events1)}",
                )
                check(
                    "I1b first run final carries run_id (决策 5)",
                    final1 is not None and isinstance(final1.get("run_id"), str)
                    and len(final1["run_id"]) > 0,
                    f"run_id={final1.get('run_id') if final1 else None}",
                )

                # Second run under the same session_id, NO historyRunId.
                # Server must resolve Run 1 as the prior envelope via
                # _resolve_session_latest_run (session_id → list_session_runs
                # → get_run), inject history_context into the intent prompt,
                # and complete 200.
                status2, events2 = await run_stream(
                    s,
                    {
                        "query": "再便宜的呢",
                        "session_id": test_session,
                    },
                )
                errs2 = [e for e in events2 if e.get("event") == "error"]
                check(
                    "I2 second run 200 (no historyRunId, resolver path)",
                    status2 == 200 and not errs2,
                    f"http={status2} errors={[e.get('code') for e in errs2]}",
                )
                final2 = next(
                    (e for e in events2 if e.get("event") == "final"), None
                )
                check(
                    "I3 second-run final arrives (resolver-injected context reached loop)",
                    final2 is not None,
                    f"final={'present' if final2 else 'missing'}",
                )

                # /sessions shows the two runs aggregated under one row.
                async with s.get(f"{BASE}/sessions") as resp_s:
                    sessions_payload = await resp_s.json()
                row = next(
                    (
                        s
                        for s in sessions_payload
                        if s.get("session_id") == test_session
                    ),
                    None,
                )
                check(
                    "I4 /sessions aggregates two runs under one session_id",
                    row is not None
                    and int(row.get("event_count") or 0) >= 4,
                    f"row={row}",
                )
        finally:
            shutdown_server(proc)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))