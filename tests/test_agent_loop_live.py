"""Live integration — one real selection run through the LOOP runtime (1.10).

Gated by LLM_API_KEY (+ a wired selection model in data/selection/
model_config.json, mirroring the probe's boot path). The run uses the real
aimux→langchain adapter + real fixture tools; asserts ≥1 real tool_call and
that the full event sequence persisted via the server contract can be
replayed through GET /sessions/{id}/events.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest
from aiohttp import web

import agent.server as srv
from agent.agents.loop_runtime import stream_run_loop
from agent.llm import AimuxChatModel, build_model_from_args
from agent.providers_store import effective_agent_model, find_api_key, load_model_config

pytestmark = pytest.mark.live


def _build_model() -> AimuxChatModel:
    entry = effective_agent_model(load_model_config(), "selection")
    provider = entry.get("provider")
    model_id = entry.get("model")
    assert provider and model_id, "no selection model wired"
    return AimuxChatModel(
        build_model_from_args(
            provider_name=provider,
            api_key=find_api_key(provider) or os.environ.get("LLM_API_KEY"),
            model_id=model_id,
            base_url=entry.get("base_url") or None,
        )
    )


def _require_live() -> None:
    if not os.environ.get("LLM_API_KEY"):
        pytest.skip("live test skipped — missing LLM_API_KEY")
    try:
        _build_model()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"live test skipped — no wired selection model ({e})")


@pytest.mark.asyncio
async def test_loop_run_full_chain_real_tools(tmp_path) -> None:
    _require_live()
    # isolate the sqlite db — _connect reads store._DEFAULT_DB (patching
    # _DATA_DIR would not affect it: _DEFAULT_DB is computed at import)
    import agent.persistence.store as store

    old_db = store._DEFAULT_DB
    store._DEFAULT_DB = tmp_path / "memory.db"

    session_id = f"loop-live-{uuid.uuid4()}"
    run_id = str(uuid.uuid4())
    model = _build_model()
    events: list[dict] = []
    try:
        async for ev in stream_run_loop(
            query="蓝牙耳机 预算 3000",
            session_id=session_id,
            run_id=run_id,
            history_context="",
            agents_models={"selection": model},
        ):
            events.append(ev)
    finally:
        store._DEFAULT_DB = old_db

    kinds = [e.get("event") for e in events]
    assert "error" not in kinds, f"run errored: {[e for e in events if e.get('event')=='error']}"
    assert "turn_start" in kinds
    tool_calls = [e for e in events if e.get("event") == "tool_call"]
    assert len(tool_calls) >= 1, "expected ≥1 real tool call"
    tool_results = [e for e in events if e.get("event") == "tool_result"]
    assert len(tool_results) >= 1
    assert kinds[-1] == "final"
    final = events[-1]
    assert final["decision"] in {"go", "caution", "no-go"}
    assert isinstance(final["candidates"], list)
    assert final["market_summary"]
    # when the model used the structured exit, the final envelope must
    # honor it (submit_report's decision is the terminal truth source)
    submit_calls = [e for e in tool_calls if e.get("tool") == "submit_report"]
    if submit_calls:
        args = submit_calls[-1].get("args") or {}
        assert args.get("decision") == final["decision"], (
            f"final decision {final['decision']!r} ignored submit_report "
            f"decision {args.get('decision')!r}"
        )
        assert final["report"].get("seed_keyword") == (args.get("seed_keyword") or final["report"].get("seed_keyword"))

    # server persistence + replay contract: the events the loop emitted must
    # be replayable through the server's messages table.
    from agent.persistence import save_event, list_session_events

    for i, ev in enumerate(events, start=1):
        save_event(
            session_id=session_id,
            run_id=run_id,
            event_type=ev["event"],
            sequence=i,
            payload=ev,
        )
    replayed = list_session_events(session_id=session_id)
    assert len(replayed) == len(events)
    assert replayed[0]["event_type"] == "turn_start"
    assert replayed[-1]["event_type"] == "final"


@pytest.mark.asyncio
async def test_memory_graph_supersedes_across_two_runs(tmp_path) -> None:
    """Live 剧本 — 同 session 同关键词连跑两次选品：第二次 run 落库后
    entity_edges 出现 supersedes / decided 边，graph_traverse 工具能渲染
    因果路径（"上次推荐的为什么没上榜"类问题的数据面）。"""
    _require_live()
    import agent.persistence.store as store
    from agent.agents.memory_graph import materialize_edges_for_run, traverse_edges
    from agent.agents.tools import build_agent_tools
    from agent.persistence import save_event, save_run

    old_db = store._DEFAULT_DB
    db = tmp_path / "memory.db"
    store._DEFAULT_DB = db

    session_id = f"mg-live-{uuid.uuid4()}"
    model = _build_model()
    persisted: list[str] = []
    try:
        for i in range(2):
            # The model occasionally ends a run without scoring (no
            # score_candidates call and no parseable 候选 table) — retry once
            # per round; if it never scores, skip honestly instead of
            # pretending the wiring failed.
            run_id = ""
            final: dict = {}
            for attempt in range(2):
                run_id = str(uuid.uuid4())
                events: list[dict] = []
                async for ev in stream_run_loop(
                    query="蓝牙耳机 预算 3000",
                    session_id=session_id,
                    run_id=run_id,
                    history_context="",
                    agents_models={"selection": model},
                ):
                    events.append(ev)
                final = events[-1]
                assert final.get("event") == "final", (
                    f"run {i + 1} attempt {attempt + 1} did not finish: {final}"
                )
                if final["candidates"]:
                    break
            else:
                pytest.skip(
                    f"run {i + 1}: model produced no scored candidates in 2 attempts "
                    "(wiring is SEAM-covered; rerun when the model cooperates)"
                )
            # mirror the server throat's full wiring: events into
            # messages first (the supersedes JOIN locates prior runs through
            # this table), then save_run + materialize on final
            for seq, ev in enumerate(events, start=1):
                save_event(
                    session_id=session_id,
                    run_id=run_id,
                    event_type=ev.get("event", "unknown"),
                    sequence=seq,
                    payload=ev,
                )
            report = final.get("report") or {}
            envelope = {
                "report": report,
                "candidates": final["candidates"],
                "intent": None,
            }
            keyword = str(report.get("seed_keyword") or "")
            save_run(
                query="蓝牙耳机 预算 3000",
                seed_keyword=keyword,
                decision=str(final.get("decision") or ""),
                report=envelope,
                run_id=run_id,
            )
            materialize_edges_for_run(
                run_id=run_id,
                session_id=session_id,
                keyword=keyword,
                decision=str(final.get("decision") or ""),
                report=envelope,
                candidates=final["candidates"],
            )
            persisted.append(run_id)

        run_id = persisted[-1]
        gt = [t for t in build_agent_tools(db_path=db) if t.name == "graph_traverse"][0]
        text = gt.invoke({"entity_type": "run", "entity_id": run_id, "hops": 1})
        assert "decided" in text, f"graph_traverse lost the decided edge: {text!r}"

        superseded = traverse_edges(
            entity_type="run", entity_id=run_id, rel="supersedes", db_path=db
        )
        assert superseded, "second run should supersede the first (same session+keyword)"
    finally:
        store._DEFAULT_DB = old_db
