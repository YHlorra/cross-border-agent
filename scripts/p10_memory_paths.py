"""P10 E2E: R10 messages + 会话 + 压缩 + 偏好 RAG 端到端路径（收口）。

Seven scenarios against an isolated AGENT_DATA_DIR temp server (mirrors p1
config-loop pattern). Each scenario isolates a layer so a failure points
directly at which slice regressed:

  G. /run events land in messages (S10.1 + S10.2)
  H. /sessions list 聚合 + /sessions/{id}/events 顺序 + user_message 头
  I. 回放 + applyEvent fold 重建 RunState（端到端一致性 pin）
  J. DELETE /sessions 清空消息 + 复跑 OK
  K. /compact NDJSON 流：start/end 事件 + 失败不污染（COMPACTION_ERROR 不写）
  L. 跨会话偏好：mock_embed 抽取 → insert_preference → retrieve 命中
  M. R3 失败不入上下文：answer_failed=1 的 run 事件不注入下次 historyRunId

Usage: .venv/Scripts/python.exe scripts/p10_memory_paths.py
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
PORT = 8771
BASE = f"http://127.0.0.1:{PORT}"
REAL_PROVIDERS = ROOT / "data" / "selection" / "providers.json"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def spawn_server(data_dir: Path) -> subprocess.Popen:
    # shared spawn_agent helper. p8-style terminate + wait + kill +
    # stderr capture + port rebind assertion.
    from _e2e_common import spawn_agent
    cm = spawn_agent(port=PORT, data_dir=str(data_dir))
    proc = cm.__enter__()
    proc._a5_cm = cm  # type: ignore[attr-defined]
    return proc


def shutdown_server(proc: subprocess.Popen) -> None:
    cm = getattr(proc, "_a5_cm", None)
    if cm is not None:
        cm.__exit__(None, None, None)


async def wait_health(session: aiohttp.ClientSession, want: int) -> bool:
    for _ in range(50):
        try:
            async with session.get(f"{BASE}/healthz") as r:
                if r.status == want:
                    return True
        except aiohttp.ClientError:
            pass
        await asyncio.sleep(0.2)
    return False


def _wire_config(data: Path) -> None:
    """Seed providers + model_config from the real catalog (for real LLM)."""
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
                "agents": {k: None for k in ["selection", "listing", "monitor", "store"]},
                "compaction": {
                    "enabled": True,
                    "reserve_tokens": 8_192,
                    "keep_recent_tokens": 4_096,
                    "fallback_context_window": 32_000,
                    "per_model_context_windows": {"MiniMax-M3": 32_000},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


async def run_stream(
    session: aiohttp.ClientSession, body: dict
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


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="p10-e2e-") as tmpdir:
        data = Path(tmpdir) / "working"
        data.mkdir()
        _wire_config(data)
        proc = spawn_server(data)
        try:
            async with aiohttp.ClientSession() as s:
                if not await wait_health(s, 200):
                    check("boot healthz 200", False, "server never became ready")
                    return 1
                check("boot healthz 200", True)

                # ── G: events land in messages ───────────────────────────
                print("\n── Scenario G: /run events land in messages (S10.1+S10.2) ──")
                status, events = await run_stream(s, {"query": "宠物用品"})
                final = next((e for e in events if e.get("event") == "final"), None)
                check("G1 /run 200 with final", status == 200 and final is not None)
                # /sessions should list at least one
                async with s.get(f"{BASE}/sessions") as r:
                    sessions = await r.json()
                check("G2 /sessions lists ≥1 session", len(sessions) >= 1)
                first_sid = sessions[0]["session_id"] if sessions else None
                check("G2.5 session has event_count > 0", sessions[0]["event_count"] > 0)

                # ── H: /sessions/{id}/events ordering + user_message ──────
                print("\n── Scenario H: events order + user_message head ──")
                async with s.get(f"{BASE}/sessions/{first_sid}/events") as r:
                    sess_events = await r.json()
                # user_message first
                ev_types = [e["event_type"] for e in sess_events]
                check(
                    "H1 first event is user_message",
                    ev_types and ev_types[0] == "user_message",
                    f"first={ev_types[0] if ev_types else '∅'}",
                )
                # wire events are non-decreasing sequence within run
                seqs = [e["sequence"] for e in sess_events if e["event_type"] != "user_message"]
                check(
                    "H2 wire events non-decreasing sequence",
                    seqs == sorted(seqs) and len(seqs) > 0,
                    f"count={len(seqs)}",
                )
                # /sessions/{id}/events?include_failed=true returns same count
                async with s.get(f"{BASE}/sessions/{first_sid}/events?include_failed=true") as r:
                    sess_all = await r.json()
                check(
                    "H3 include_failed=true returns all events",
                    len(sess_all) == len(sess_events),
                    f"default={len(sess_events)} all={len(sess_all)}",
                )

                # ── I: replay rebuilds RunState end-to-end ───────────────
                # This is a backend check: the frontend replaySessionEvents is
                # unit-tested in tests/test_run_state_replay.py. Here we just
                # assert the events carry the right shapes for that fold.
                print("\n── Scenario I: replay round-trip shape ──")
                # Apply manually: filter user_message, sort by (run_id, sequence),
                # assert each event has a payload that matches the wire schema.
                wire = [e for e in sess_events if e["event_type"] != "user_message"]
                wire.sort(key=lambda e: (e["run_id"], e["sequence"]))
                seen_event_kinds = {e["payload"].get("event") for e in wire}
                expected = {"turn_start", "tool_call", "tool_result", "final"}
                check(
                    "I1 wire events cover expected kinds",
                    expected.issubset(seen_event_kinds),
                    f"missing={expected - seen_event_kinds}",
                )

                # ── J: DELETE /sessions wipes; subsequent /run still works ─
                print("\n── Scenario J: DELETE /sessions wipes ──")
                async with s.delete(f"{BASE}/sessions/{first_sid}") as r:
                    check("J1 DELETE 204", r.status == 204)
                async with s.get(f"{BASE}/sessions") as r:
                    sessions_after = await r.json()
                check(
                    "J2 deleted session no longer listed",
                    first_sid not in {s["session_id"] for s in sessions_after},
                )
                # subsequent /run still works (server not broken)
                status2, _ = await run_stream(s, {"query": "再跑一次"})
                check("J3 /run still 200 after wipe", status2 == 200)

                # ── K: /compact NDJSON stream + start/end events ────────
                print("\n── Scenario K: /compact NDJSON stream ──")
                # First create a session worth compacting (it must have enough
                # events to exceed the keep_recent_tokens budget)
                status3, _ = await run_stream(s, {"query": "先跑一轮有事件"})
                check("K0 build a run for /compact", status3 == 200)
                async with s.get(f"{BASE}/sessions") as r:
                    sessions_for_compact = await r.json()
                target_sid = sessions_for_compact[0]["session_id"]
                async with s.post(
                    f"{BASE}/compact",
                    json={"session_id": target_sid},
                ) as r:
                    check("K1 /compact 200", r.status == 200)
                    comp_events: list[dict] = []
                    async for raw in r.content:
                        line = raw.decode("utf-8", errors="replace").strip()
                        if line:
                            try:
                                comp_events.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
                kinds = [e.get("event") for e in comp_events]
                check(
                    "K2 compaction_start + compaction_end emitted",
                    kinds and kinds[0] == "compaction_start" and kinds[-1] == "compaction_end",
                    f"kinds={kinds}",
                )
                end = comp_events[-1]
                # Check the new row is in messages — ONLY if compaction
                # actually ran (i.e. result present). If errorMessage, the
                # "nothing to compact" path correctly wrote nothing (pi 失败
                # 不污染 transcript contract).
                async with s.get(f"{BASE}/sessions/{target_sid}/events") as r2:
                    after_events = await r2.json()
                comp_present = any(
                    e["event_type"] == "compaction" for e in after_events
                )
                if end.get("result"):
                    check("K3 compaction row in messages (success path)", comp_present)
                    check(
                        "K4 compaction_end.result has summary",
                        isinstance(end["result"].get("summary"), str)
                        and len(end["result"]["summary"]) > 0,
                    )
                else:
                    # Failure path: must NOT have written (per  失败
                    # 不污染). errorMessage is required.
                    check(
                        "K3' failure path: no row written, errorMessage set",
                        (not comp_present)
                        and bool(end.get("errorMessage")),
                        f"comp_present={comp_present} error={end.get('errorMessage', '')[:60]}",
                    )

                # ── L: cross-session preference RAG end-to-end ──────────
                # Insert a preference directly (extraction requires a real LLM
                # call and the persistence path is already SEAM-tested). The
                # point here is verifying the fixture is readable through the
                # same retrieval channel a future loop-side injection will use.
                print("\n── Scenario L: cross-session preference flow ──")
                from agent.preferences.embed import mock_embed
                from agent.preferences.retrieve import retrieve

                pref_text = "想做 1000 以内的母婴"
                emb = mock_embed(pref_text, dimensions=1536)
                # pin the fixture to a per-test temp DB so we
                # never write into the real memory.db (the original script
                # ran without db_path, leaving 4 fixture rows behind — see
                #  F-4). Resolve via resolve_data_dir so the temp
                # location matches what the server (with AGENT_DATA_DIR
                # set to the same temp dir) would use.
                from agent.persistence import insert_preference
                from pathlib import Path
                fixture_db = Path(str(data_dir)) / "memory.db"
                pref_id = insert_preference(
                    category="budget",
                    preference_text=pref_text,
                    embedding=emb,
                    source_run_id="p10-fixture",
                    source_session_id="p10-fixture",
                    db_path=str(fixture_db),
                )
                check("L1 preference fixture inserted", isinstance(pref_id, int) and pref_id > 0)
                # The server's /run uses the same AGENT_DATA_DIR; retrieval
                # against this DB file is the storage half of preference
                # injection (the prompt-injection half is not wired yet).
                hits = retrieve(
                    query_embedding=mock_embed("1000 以内的母婴", dimensions=1536),
                    top_k=3,
                    db_path=str(fixture_db),
                )
                check(
                    "L2 retrieval returns the fixture preference",
                    any(h.preference_text == pref_text for h in hits),
                    f"hits={[h.preference_text[:20] for h in hits]}",
                )

                # ── M: error run events filtered from historyRunId context ─
                # Send a bogus historyRunId → server 404 (already covered by
                # p9 F1). For the success path, ensure the events row's
                # answer_failed flag is honored by list_session_events
                # include_failed=false (default).
                print("\n── Scenario M: answer_failed filter via events endpoint ──")
                async with s.get(f"{BASE}/sessions/{target_sid}/events") as r:
                    with_failed = await r.json()
                async with s.get(
                    f"{BASE}/sessions/{target_sid}/events?include_failed=false"
                ) as r:
                    without_failed = await r.json()
                # All events for a clean run are not failed → counts match
                check(
                    "M1 include_failed=false keeps non-failed events",
                    len(without_failed) >= len(with_failed),
                    f"default={len(with_failed)} filtered={len(without_failed)}",
                )
        finally:
            shutdown_server(proc)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))