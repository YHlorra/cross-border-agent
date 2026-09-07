"""P5 E2E: error transparency paths against an isolated AGENT_DATA_DIR server.

  D. GBK-encoded request body → wire-format 400 (F2 fix; was a raw aiohttp 500)
  E. invalid provider api key → aimux APICallError streamed verbatim in /run
     (no retry, no masking — the red line)

Usage: .venv/Scripts/python.exe scripts/p5_error_paths.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
PORT = 8767
BASE = f"http://127.0.0.1:{PORT}"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


async def wait_ready(session: aiohttp.ClientSession) -> bool:
    for _ in range(50):
        try:
            async with session.get(f"{BASE}/healthz") as resp:
                if resp.status == 200:
                    return True
        except aiohttp.ClientError:
            pass
        await asyncio.sleep(0.2)
    return False


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="p5-e2e-") as tmpdir:
        data = Path(tmpdir)
        # Boot succeeds (a key IS present) — upstream calls fail with 401.
        (data / "providers.json").write_text(
            json.dumps(
                [
                    {
                        "name": "minimax_cn",
                        "label": "MiniMax CN",
                        "base_url": "https://api.minimaxi.com/v1",
                        "api_key": "sk-invalid-e2e-key",
                        "models": [{"id": "MiniMax-M3", "name": "MiniMax-M3"}],
                    }
                ]
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
                }
            ),
            encoding="utf-8",
        )
        env = {**os.environ, "AGENT_DATA_DIR": str(data), "AGENT_PORT": str(PORT)}
        # use shared helper (p8-style terminate + wait + kill + stderr
        # capture + port rebind assertion). The literal "data" / "PORT"
        # shadowing was a small footgun in the original — pass explicitly.
        from _e2e_common import spawn_agent
        cm = spawn_agent(port=PORT, data_dir=str(data))
        proc = cm.__enter__()
        proc._a5_cm = cm  # type: ignore[attr-defined]
        try:
            async with aiohttp.ClientSession() as s:
                ready = await wait_ready(s)
                check("E0 server booted with (invalid) key present", ready)
                if not ready:
                    return 1

                # ── D: GBK body → wire 400, not raw 500 ──
                gbk_body = '{"query":"宠物用品"}'.encode("gbk")
                async with s.post(
                    f"{BASE}/run",
                    data=gbk_body,
                    headers={"content-type": "application/json"},
                ) as resp:
                    text = await resp.text()
                    try:
                        wire = json.loads(text.strip().splitlines()[0])
                    except (json.JSONDecodeError, IndexError):
                        wire = {}
                    check(
                        "D1 non-UTF-8 body → HTTP 400 wire error (F2)",
                        resp.status == 400
                        and wire.get("event") == "error"
                        and bool(wire.get("code")),
                        f"status={resp.status} code={wire.get('code')}",
                    )

                # ── E: upstream 401 → APICallError passthrough ──
                events: list[dict] = []
                async with s.post(
                    f"{BASE}/run", json={"query": "宠物用品"}
                ) as resp:
                    async for raw in resp.content:
                        line = raw.decode("utf-8", errors="replace").strip()
                        if line:
                            try:
                                events.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
                errs = [e for e in events if e.get("event") == "error"]
                ok_e = bool(errs) and errs[0].get("code") == "APICallError"
                check(
                    "E1 invalid key → APICallError streamed verbatim",
                    ok_e,
                    f"code={errs[0].get('code') if errs else 'none'} "
                    f"msg_head={str(errs[0].get('message'))[:80] if errs else '-'}",
                )
                if errs:
                    low = str(errs[0].get("message", "")).lower()
                    check(
                        "E2 upstream detail visible in message",
                        any(t in low for t in ("401", "key", "auth", "invalid")),
                        f"contains auth signal",
                    )
        finally:
            cm_local = getattr(proc, "_a5_cm", None)
            if cm_local is not None:
                cm_local.__exit__(None, None, None)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
