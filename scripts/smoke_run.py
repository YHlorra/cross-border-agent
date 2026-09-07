"""P0.1 smoke client: run one selection flow and record the NDJSON event timeline.

Usage:
    .venv/Scripts/python.exe scripts/smoke_run.py [--port 8766] [--query "..."]

Streams /run line by line with arrival timestamps so we can verify:
  - which nodes light up (node_start / node_end order)
  - whether report chunks arrive incrementally (streaming) or in one burst
  - whether a `final` event is emitted (known P0.2 bug: it is not)
Exit code 0 if the stream completes; 1 on transport error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import aiohttp


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--path", default="/run", help="/run direct or /api/selection/run via proxy")
    parser.add_argument("--query", default="宠物用品 预算2000 轻小件")
    parser.add_argument("--save", help="dump all events to this JSON file")
    args = parser.parse_args()

    #  — the per-query budget/preferences wire fields are gone; put
    # such signals in the query text (the loop's agent reads them there).
    payload = {"query": args.query}
    url = f"http://127.0.0.1:{args.port}{args.path}"
    events: list[dict] = []
    arrivals: list[float] = []
    t0 = time.monotonic()

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            print(f"http={resp.status}")
            if resp.status != 200:
                print(await resp.text())
                return 1
            async for raw in resp.content:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    print(f"[{time.monotonic() - t0:7.2f}s] PARSE_ERROR {line[:120]}")
                    continue
                events.append(evt)
                arrivals.append(time.monotonic() - t0)
                et = evt.get("event", "?")
                if et == "report_chunk":
                    continue  # printed in aggregate below
                print(f"[{arrivals[-1]:7.2f}s] {et:14} {evt.get('node', ''):14} "
                      f"{str(evt.get('summary', evt.get('code', evt.get('tool', ''))))[:70]}")

    chunks = [i for i, e in enumerate(events) if e.get("event") == "report_chunk"]
    if chunks:
        times = [arrivals[i] for i in chunks]
        span = times[-1] - times[0]
        print(f"\nreport_chunk count={len(chunks)} first={times[0]:.2f}s last={times[-1]:.2f}s "
              f"span={span:.2f}s -> {'REAL streaming' if span > 1.0 else 'BURST (fake streaming)'}")
    else:
        print("\nreport_chunk count=0")
    finals = [e for e in events if e.get("event") == "final"]
    print(f"final event: {'PRESENT' if finals else 'MISSING (P0.2 known bug)'}")
    print(f"total events={len(events)} total time={time.monotonic() - t0:.2f}s")
    if args.save:
        import pathlib
        pathlib.Path(args.save).write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"events saved to {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
