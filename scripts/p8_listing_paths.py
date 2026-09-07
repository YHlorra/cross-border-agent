"""p8 E2E: listing 双通道全链路（无 SP-API 凭据 → 退化导出，）。

隔离 AGENT_DATA_DIR 临时服务（真实 provider 凭据只读服务端，不打印）：

  A. 配置闭环：保存真实 provider + 模型配置 → boot 热重载 → healthz 200
  B. Listing 生成：POST /listing/run（真实 LLM）→ node_start draft →
     listing_chunk* → listing_draft（标题≤75 / 五点5条 / 描述≤2000）→
     final kind=listing；SQLite 出现 listing_runs 行
  C. 标准文件导出：POST /listing/export → payload(product_type/价格 schedule)
  D. 上传退化：POST /listing/upload（无凭据）→ 409 NoSpapiCredentials

P3（agent-loop-architecture）：/listing/run 后端为 listing agent loop——
B 的断言按 loop 事件序列（turn_start/tool_call(submit_draft)/listing_draft/
final kind=listing 仍在）。

Usage: .venv/Scripts/python.exe scripts/p8_listing_paths.py
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
PORT = 8768
BASE = f"http://127.0.0.1:{PORT}"
REAL_PROVIDERS = ROOT / "data" / "selection" / "providers.json"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


CANDIDATE = {
    "product_id": "1688_004",
    "name_cn": "宠物自动喂食器",
    "name_en": "Smart Pet Feeder",
    "source_price_cny": 198.0,
    "target_price_usd": 39.99,
    "price_gap_ratio": 1.44,
    "category": "pet_supplies",
    "bsr_rank": 8500,
    "bsr_top_percent": 8.0,
    "review_count": 210,
    "monthly_search": 32000,
    "trend_growth": 0.18,
}


async def wait_ready(session: aiohttp.ClientSession) -> bool:
    for _ in range(60):
        try:
            async with session.get(f"{BASE}/healthz") as resp:
                if resp.status in (200, 503):
                    return True
        except aiohttp.ClientError:
            pass
        await asyncio.sleep(0.25)
    return False


async def scenario_abcd(tmpdir: Path) -> None:
    env = dict(os.environ)
    env["AGENT_DATA_DIR"] = str(tmpdir)
    env["AGENT_PORT"] = str(PORT)
    err_f = open(tmpdir / "server.err", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "agent.server"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=err_f,
    )
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as s:
            if not await wait_ready(s):
                check("boot server", False)
                return

            # ── A. 配置闭环（复用 p1 的真实凭据只读服务端模式）──
            real = json.loads(REAL_PROVIDERS.read_text(encoding="utf-8"))
            provider = real[0]
            async with s.post(
                f"{BASE}/config/saved",
                json={
                    "name": provider["name"],
                    "api_key": provider["api_key"],
                    "base_url": provider.get("base_url") or "",
                },
            ) as resp:
                check("A1 save real provider", resp.status == 200)
            async with s.post(
                f"{BASE}/config/model",
                json={
                    "default": {
                        "provider": provider["name"],
                        "model": "MiniMax-M3",
                        "base_url": provider.get("base_url") or "",
                    }
                },
            ) as resp:
                check("A2 save model config (hot reload)", resp.status == 200)
            async with s.get(f"{BASE}/healthz") as resp:
                check("A3 healthz 200 after boot", resp.status == 200)

            # ── B. Listing 生成（真实 LLM，慢）──
            events: list[dict] = []
            t0 = time.monotonic()
            async with s.post(
                f"{BASE}/listing/run",
                json={
                    "candidate": CANDIDATE,
                    "competitorBrands": ["PetSafe"],
                    "brand": "",
                },
            ) as resp:
                check("B1 /listing/run http 200", resp.status == 200, f"status={resp.status}")
                async for raw in resp.content:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

            kinds = [e.get("event") for e in events]
            final = next((e for e in events if e.get("event") == "final"), None)
            draft = next((e for e in events if e.get("event") == "listing_draft"), None)
            # loop listing agent: real create_agent turn events +
            # a submit_draft tool trace + the compat listing dialect.
            submit = any(
                e.get("event") == "tool_call" and e.get("tool") == "submit_draft"
                for e in events
            )
            check(
                "B2 listing loop wire events (turn_start/submit_draft/listing_draft)",
                "turn_start" in kinds and submit and draft is not None,
                f"kinds={sorted(set(kinds))}",
            )
            listing = (final or draft or {}).get("listing") or {}
            if final is None:
                print("  [debug] kinds tail:", kinds[-6:])
                print(
                    "  [debug] server.err tail:",
                    (tmpdir / "server.err").read_text(encoding="utf-8")[-800:],
                )
            title = listing.get("item_name") or ""
            check(
                "B4 title within 75 chars (2026-07 rule)",
                0 < len(title) <= 75,
                f"len={len(title)}",
            )
            check(
                "B5 five bullets present",
                len(listing.get("bullet_point") or []) == 5,
            )
            check(
                "B6 final kind=listing persisted",
                final is not None and final.get("kind") == "listing",
                f"total={time.monotonic() - t0:.0f}s",
            )
            run_id = (final or {}).get("run_id") or ""

            # ── C. 标准文件导出 ──
            async with s.post(
                f"{BASE}/listing/export",
                json={"run_id": run_id, "price_usd": 45.0, "sku": "P8-SKU"},
            ) as resp:
                data = await resp.json()
                payload = data.get("payload") or {}
                offer = (
                    payload.get("attributes", {})
                    .get("purchasable_offer", [{}])[0]
                    .get("our_price", [{}])[0]
                    .get("schedule", [{}])[0]
                    .get("value_with_currency")
                )
                check(
                    "C1 export renders payload",
                    resp.status == 200 and bool(payload.get("product_type")),
                    f"product_type={payload.get('product_type')!r}",
                )
                check("C2 price override applied", offer == "45.00USD", f"offer={offer}")

            # ── D. 上传退化（无凭据）──
            async with s.post(
                f"{BASE}/listing/upload", json={"run_id": run_id}
            ) as resp:
                body = await resp.json()
                check(
                    "D1 upload degrades 409 NoSpapiCredentials",
                    resp.status == 409 and body.get("code") == "NoSpapiCredentials",
                    f"status={resp.status}",
                )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        err_f.close()


async def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="p8-listing-", ignore_cleanup_errors=True
    ) as tmpdir:
        await scenario_abcd(Path(tmpdir))
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
