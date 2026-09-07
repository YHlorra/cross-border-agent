"""P1 closed-loop E2E: no-key startup → wire transparency → config save → hot reload → run.

Three scenarios against isolated AGENT_DATA_DIR temp servers (never touches the
real providers.json):

  A. empty config dir  → boot OK but no models → /run returns 200 + wire
     StartupConfigError line (frontend renders ErrorView from the stream)
  B. provider saved without api_key + default model wired → boot fails →
     /healthz 503 wire, /run 503 + wire body (the case runSelection used to
     discard behind NetworkError)
  C. closed loop on top of B: POST /config/saved + /config/model with the real
     provider record (read server-side, never printed) → boot() hot reload →
     /healthz 200 → /run completes. C4 asserts the loop's dual-layer events
     (turn_start + tool_call + tool_result) before final.

Usage: .venv/Scripts/python.exe scripts/p1_closed_loop.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
PORT = 8767
BASE = f"http://127.0.0.1:{PORT}"
REAL_PROVIDERS = ROOT / "data" / "selection" / "providers.json"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def spawn_server(data_dir: Path) -> subprocess.Popen:
    # thin wrapper around the shared ``spawn_agent`` context manager
    # so callers don't need to know about the terminate / wait / kill
    # ordering. The manager captures stderr for diagnostic dumps on
    # non-zero exit; asserts port rebind at the end.
    from _e2e_common import spawn_agent

    extra_env = {"AGENT_BRIDGE_DEBUG": "0"}
    cm = spawn_agent(
        port=PORT,
        data_dir=str(data_dir),
        extra_env=extra_env,
    )
    proc = cm.__enter__()
    # Stash the cm so the test's finally can call __exit__.
    proc._a5_cm = cm  # type: ignore[attr-defined]
    return proc


def shutdown_server(proc: subprocess.Popen) -> None:
    """Companion to spawn_server — runs the p8-style triple-保险."""
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


async def run_stream(session: aiohttp.ClientSession) -> tuple[int, list[dict]]:
    events: list[dict] = []
    async with session.post(
        f"{BASE}/run", json={"query": "宠物用品"}
    ) as resp:
        status = resp.status
        async for raw in resp.content:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return status, events


async def scenario_a(tmp: Path) -> None:
    print("\n── Scenario A: empty config dir ──")
    data = tmp / "a"
    data.mkdir()
    proc = spawn_server(data)
    try:
        async with aiohttp.ClientSession() as s:
            h = await wait_health(s, 200)
            check("A1 healthz 200 (boot ok, no models)", h is not None and h.get("status") == "ok")
            status, events = await run_stream(s)
            errs = [e for e in events if e.get("event") == "error"]
            ok = status == 200 and errs and errs[0]["code"] == "StartupConfigError"
            check(
                "A2 /run streams wire StartupConfigError",
                bool(ok),
                f"http={status} code={errs[0]['code'] if errs else 'none'} "
                f"missing={errs[0].get('missing') if errs else '-'}",
            )
    finally:
        shutdown_server(proc)


async def scenario_b_and_c(tmp: Path) -> None:
    print("\n── Scenario B: provider without key (boot failure) ──")
    data = tmp / "b"
    data.mkdir()
    (data / "providers.json").write_text(
        json.dumps(
            [
                {
                    "name": "minimax_cn",
                    "label": "MiniMax CN",
                    "base_url": "https://api.minimaxi.com/v1",
                    "api_key": "",
                    "models": [{"id": "MiniMax-M3", "name": "MiniMax-M3"}],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data / "model_config.json").write_text(
        json.dumps(
            {
                "default": {
                    "provider": "minimax_cn",
                    "model": "MiniMax-M3",
                    "base_url": "https://api.minimaxi.com/v1",
                },
                "agents": {"selection": None, "listing": None, "monitor": None, "store": None},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    proc = spawn_server(data)
    try:
        async with aiohttp.ClientSession() as s:
            h = await wait_health(s, 503)
            check(
                "B1 healthz 503 wire StartupConfigError",
                h is not None and h.get("code") == "StartupConfigError",
                f"code={h.get('code') if h else 'no-resp'} missing={h.get('missing') if h else '-'}",
            )
            status, events = await run_stream(s)
            errs = [e for e in events if e.get("event") == "error"]
            ok = status == 503 and errs and errs[0]["code"] == "StartupConfigError"
            check(
                "B2 /run 503 body carries wire error (client fix target)",
                bool(ok),
                f"http={status} code={errs[0]['code'] if errs else 'none'}",
            )

            print("\n── Scenario C: config save → hot reload → run ──")
            real = json.loads(REAL_PROVIDERS.read_text(encoding="utf-8"))
            rec = next(r for r in real if r.get("name") == "minimax_cn")
            async with s.post(
                f"{BASE}/config/saved",
                json={
                    "name": rec["name"],
                    "label": rec.get("label"),
                    "base_url": rec.get("base_url"),
                    "api_key": rec["api_key"],
                    "models": rec.get("models", []),
                },
            ) as resp:
                check("C1 save provider via /config/saved", resp.status == 200)
            async with s.post(
                f"{BASE}/config/model",
                json={
                    "default": {
                        "provider": "minimax_cn",
                        "model": "MiniMax-M3",
                        "base_url": "https://api.minimaxi.com/v1",
                    },
                    "agents": {"selection": None, "listing": None, "monitor": None, "store": None},
                },
            ) as resp:
                body = await resp.json()
                check(
                    "C2 save model config (triggers boot hot reload)",
                    resp.status == 200,
                    str(body.get("default", {})),
                )
            async with s.get(f"{BASE}/healthz") as resp:
                check("C3 healthz 200 after hot reload", resp.status == 200)

            status, events = await run_stream(s)
            kinds = [e.get("event") for e in events]
            has_final = "final" in kinds
            dual = (
                "turn_start" in kinds
                and "tool_call" in kinds
                and "tool_result" in kinds
            )
            check(
                "C4 /run completes end to end (loop runtime, dual-layer events)",
                status == 200 and dual and has_final,
                f"kinds={sorted(set(kinds))} final={has_final}",
            )
    finally:
        shutdown_server(proc)


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="p1-e2e-") as tmpdir:
        await scenario_a(Path(tmpdir))
        await scenario_b_and_c(Path(tmpdir))
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
