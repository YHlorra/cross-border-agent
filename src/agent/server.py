"""aiohttp NDJSON bridge for the selection agent.

Routes:
- POST /run       NDJSON stream of events (node_start / tool_call / report_chunk / final / error)
- GET  /history   JSON array of past runs
- GET  /config    effective LLM_* env snapshot (no secret)
- POST /config/providers  curated provider catalog
- POST /config/models     real model list for a registry provider (validates key)
- POST /config/test       minimal generation to prove connectivity
- POST /config/save       persist .env.local + hot-reload both models
- GET  /healthz   200 on successful boot, 503 with structured error otherwise

Run via: ``python -m agent.server`` (loads ``.env.local`` first).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import urllib.parse
import uuid
from contextlib import aclosing
from typing import Any

from aiohttp import web
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, ValidationError

# dotenv 必须先于任何配置读取（曾经 persistence 先 import，dispatcher
# 在模块加载末尾定型，.env.local 的 DATABASE_URL 永远不生效——无声回退）。
load_dotenv(".env.local")
load_dotenv()  # fallback to .env if present
if not os.environ.get("DATABASE_URL"):
    raise RuntimeError(
        "DATABASE_URL is required (PG-only persistence). "
        "Set it in .env.local and start PG with scripts/pg_up.bat"
    )

from .errors import to_wire
from .agents.general_agent import stream_general_loop
from .agents.listing_agent import stream_run_listing_loop
from .agents.loop_runtime import stream_run_loop
from .agents.memory_graph import materialize_edges_for_run
from .agents.title import generate_session_title
from .context import render_history_context
from .config_api import list_models, list_providers, save_config, check_connection
from . import providers_store
from . import durable
from . import observability
from .providers_store import (
    AGENT_KEYS,
    effective_agent_model,
    find_api_key,
    load_model_config,
    save_model_config,
)
from .llm import (
    AimuxChatModel,
    StartupConfigError,
    build_cheap_model,
    build_primary_model,
    build_model_from_args,
    current_config_snapshot,
)
from .persistence import (
    delete_listing_run,
    delete_preference,
    delete_session_events,
    get_listing_run,
    get_session_meta,
    rename_listing_run,
    update_session_meta,
    get_run,
    insert_preference,
    list_active_preferences,
    list_approvals,
    list_listing_runs,
    list_runs,
    list_session_events,
    list_session_runs,
    list_sessions,
    save_event,
    save_listing_run,
    save_run,
    update_preference,
)
from .preferences.embed import mock_embed, real_embed
from .compaction.prepare import prepare_compaction
from .compaction.policy import should_compact
from .compaction.quality import CompactionError
from .compaction.types import CompactionSettings
from .providers_store import load_compaction_settings
from .listing.channel import resolve_upload_channel
from .listing.export import render_export
from .listing.regen import is_regen_field, regenerate_listing_field
from .listing.schema import ListingFacts, ListingOutput, load_market_config
from .listing.spapi import SpapiClient
from .listing.spapi_store import load_spapi_creds, save_spapi_creds
from .state import ProductCandidate


log = logging.getLogger("agent.server")

# Built once per process. None means boot failed; the relevant error is
# stored in ``boot_error`` so /healthz can return it.
# agents_models maps a business stage (selection / listing / ...) to the
# AimuxChatModel that stage's agent actually uses.
agents_models: dict[str, AimuxChatModel] = {}
boot_error: dict[str, Any] | None = None


def boot() -> None:
    global agents_models, boot_error
    try:
        model_cfg = load_model_config()
        resolved: dict[str, AimuxChatModel] = {}
        for key in AGENT_KEYS:
            entry = effective_agent_model(model_cfg, key)
            provider = entry.get("provider")
            model_id = entry.get("model")
            if not provider or not model_id:
                continue  # stage skipped — no model wired
            api_key = find_api_key(provider) or os.environ.get("LLM_API_KEY")
            if not api_key:
                raise StartupConfigError([f"api key for provider {provider}"])
            resolved[key] = AimuxChatModel(
                build_model_from_args(
                    provider_name=provider,
                    api_key=api_key,
                    model_id=model_id,
                    base_url=entry.get("base_url") or None,
                )
            )
        agents_models = resolved
        boot_error = None
    except StartupConfigError as e:
        agents_models = {}
        boot_error = to_wire(e)
        log.error("Boot failed: %s", e)
    except Exception as e:  # noqa: BLE001 — surface anything else
        agents_models = {}
        boot_error = to_wire(e)
        log.exception("Boot failed unexpectedly")


boot()


# ──── Handlers ───────────────────────────────────────────────────────────────


async def healthz(_req: web.Request) -> web.Response:
    if boot_error is None:
        # A2b — echo the per-launch nonce so the bridge can verify it is
        # talking to the child it just spawned (kills the stale-server
        # adoption loophole: a healthy orphan from a previous dev session
        # also returns 200, but its nonce will not match).
        return web.json_response(
            {
                "status": "ok",
                "nonce": os.environ.get("AGENT_LAUNCH_NONCE", ""),
            }
        )
    return web.json_response(boot_error, status=503)


async def config_snapshot(_req: web.Request) -> web.Response:
    snapshot = current_config_snapshot()
    # The runtime-resolved selection model — the single source of truth the
    # frontend badge displays. The config page persists to model_config.json
    # (not .env.local), so env-only fields routinely show "not configured"
    # while a working model is wired; effective_selection fixes that mismatch.
    if "selection" in agents_models:
        try:
            entry = effective_agent_model(load_model_config(), "selection")
            if entry.get("provider") and entry.get("model"):
                snapshot["effective_selection"] = {
                    "provider": entry["provider"],
                    "model": entry["model"],
                }
        except Exception:  # noqa: BLE001 — badge is best-effort
            pass
    return web.json_response(snapshot)


# ──── LLM config-page handlers ────────────────────────────────────────────────


def _wire_error_response(exc: BaseException) -> web.Response:
    """Convert any exception to a structured JSON error, mirroring /run."""
    wire = to_wire(exc)
    status = wire.get("status") if isinstance(wire.get("status"), int) else 400
    return web.json_response(wire, status=status)


# wire contract models. extra="forbid" so unknown fields surface as 400
# instead of being silently dropped (root cause). Field names are kept
# identical to the current wire format (camel/snake mix) — the model freezes
# the existing surface; the R11 alias refactor (RunBody alias_generator=to_camel)
# is the planned single source of truth going forward.
class RunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    session_id: str | None = None
    historyRunId: str | None = None


class ListingRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: dict
    marketCode: str = "US"
    brand: str = ""
    competitorBrands: list[str] = []
    session_id: str | None = None


class ChatBody(BaseModel):
    """general-agent-chat — plain-text chat channel body. Same surface as
    RunBody minus historyRunId (chat builds its own prose transcript)."""

    model_config = ConfigDict(extra="forbid")

    query: str
    session_id: str | None = None


def _bad_request_wire(message: str, code: str = "BadRequest") -> dict:
    """Single source of truth for 400 NDJSON wire events."""
    return {
        "event": "error",
        "code": code,
        "message": message,
        "status": 400,
    }


async def _bad_request_ndjson(req: web.Request, message: str) -> web.StreamResponse:
    """Write one 400 NDJSON wire-error frame (used by /chat body validation —
    the same wire shape _validation_400 emits for pydantic failures)."""
    resp = web.StreamResponse(
        status=400,
        headers={"Content-Type": "application/x-ndjson"},
    )
    await resp.prepare(req)
    await resp.write(
        (json.dumps(_bad_request_wire(message, "BadRequest"), ensure_ascii=False) + "\n").encode("utf-8")
    )
    await resp.write_eof()
    return resp


async def _validation_400(req: web.Request, exc: ValidationError) -> web.StreamResponse:
    """Emit a 400 NDJSON response for a pydantic ValidationError. Mirrors the
    JSONDecodeError handling at /run:482-496 — same wire shape so the frontend
    error surface is uniform.
    """
    first = exc.errors()[0] if exc.errors() else {"msg": str(exc)}
    loc = ".".join(str(p) for p in first.get("loc", [])) or "body"
    msg = f"request body validation failed at {loc}: {first.get('msg', str(exc))}"
    resp = web.StreamResponse(
        status=400,
        headers={"Content-Type": "application/x-ndjson"},
    )
    await resp.prepare(req)
    await resp.write(
        (json.dumps(_bad_request_wire(msg, "BadRequest"), ensure_ascii=False) + "\n").encode("utf-8")
    )
    await resp.write_eof()
    return resp


def _spawn_title_task(
    model: "AimuxChatModel", query: str, session_id: str
) -> None:
    """Fire-and-forget title writer.

    Schedules a one-shot LLM call that proposes a ≤16-char Chinese task
    title and writes it to session_meta. Re-checks the title is still
    empty right before writing (race: user may have manually renamed
    while the LLM call was in flight). All exceptions are logged and
    swallowed — a failed title must never break the run.
    """
    async def _runner() -> None:
        try:
            title = await generate_session_title(model, query)
        except Exception:  # noqa: BLE001
            log.exception("title generation crashed")
            return
        if not title:
            return
        try:
            # Re-check: a user rename during the LLM call wins.
            current = get_session_meta(session_id=session_id)
            if current.get("title"):
                return
            update_session_meta(session_id=session_id, fields={"title": title})
        except Exception:  # noqa: BLE001
            log.exception("title write failed (non-fatal)")

    try:
        task = asyncio.create_task(_runner())
    except RuntimeError:
        # No event loop — extremely rare; drop silently.
        return
    # done-callback for the case asyncio reports an unhandled error
    # before our except branches see it (defence in depth).
    def _on_done(t: asyncio.Task) -> None:
        try:
            t.result()
        except Exception:  # noqa: BLE001
            log.exception("title task unhandled error")

    task.add_done_callback(_on_done)


def _resolve_session_latest_run(session_id: str) -> dict | None:
    """Server-side session_id → prior run resolver.

    When the caller does not supply an explicit ``historyRunId`` (which would
    404 fail-closed if it pointed at a session id instead of a run id — the
    old bug), fall back to the latest *successful* run of this session.
    Successful = not answer_failed and has a final event.
    failure rows persisted but out of context, so picking the latest
    successful run is the right semantic for follow-ups.

    Industry alignment: codex ``ThreadResumeParams{thread_id}`` resumes from
    disk by thread id, enthusiast ``conversationId`` rebuilds the full
    message list from DB on continue, LangGraph checkpointer keys state on
    ``thread_id`` — three independent precedents for "single id, server
    authoritative history resolution".

    Returns the full envelope dict (get_run shape) so ``render_history_context``
    can be applied directly, or ``None`` if no eligible run exists (first
    run of a new session).
    """
    runs = list_session_runs(session_id=session_id)
    if not runs:
        return None
    successful = [r for r in runs if not r["answer_failed"] and r.get("final_payload") is not None]
    if not successful:
        return None
    # list_session_runs has no explicit ORDER BY (relies on SQLite's GROUP BY
    # scan order). Pick by creation time so the latest run wins regardless.
    successful.sort(key=lambda r: r["first_event_at"])
    return get_run(successful[-1]["run_id"])


def _chat_history_context(events: list[dict], *, max_chars: int = 4000) -> str:
    """general-agent-chat prose transcript of recent chat turns from
    persisted session events. Pure function over ``list_session_events`` rows
    (testable without SQLite): only ``user_message`` queries and chat
    ``final`` texts become lines; selection/listing runs stay out (they have
    their own history channels). Oldest-last, capped to the most recent
    ``max_chars`` so long sessions cannot flood the system prompt."""
    lines: list[str] = []
    for ev in events:
        payload = ev.get("payload") or {}
        if ev.get("event_type") == "user_message":
            if payload.get("kind") in ("chat_turn", None):
                q = str(payload.get("query") or "").strip()
                if q:
                    lines.append(f"用户: {q}")
        elif ev.get("event_type") == "final" and payload.get("kind") == "chat":
            text = " ".join(str(payload.get("text") or "").split())
            if text:
                lines.append(f"助手: {text[:600]}")
    transcript = "\n".join(lines)
    return transcript[-max_chars:]


async def config_providers(_req: web.Request) -> web.Response:
    try:
        return web.json_response({"providers": list_providers()})
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def config_models(req: web.Request) -> web.Response:
    try:
        body = await req.json()
        name = (body.get("provider") or "").strip()
        key = (body.get("api_key") or "").strip()
        if not name or not key:
            raise ValueError("provider and api_key are required")
        models = list_models(
            provider_name=name,
            api_key=key,
            base_url=body.get("base_url") or None,
        )
        return web.json_response({"provider": name, "models": models})
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def config_test(req: web.Request) -> web.Response:
    try:
        body = await req.json()
        name = (body.get("provider") or "").strip()
        key = (body.get("api_key") or "").strip()
        model_id = (body.get("model") or "").strip()
        if not name or not key or not model_id:
            raise ValueError("provider, api_key and model are required")
        result = check_connection(
            provider_name=name,
            api_key=key,
            model_id=model_id,
            base_url=body.get("base_url") or None,
        )
        return web.json_response(result)
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def config_save(req: web.Request) -> web.Response:
    try:
        body = await req.json()
        saved = save_config(body)
        # Mirror into the running process env, then rebuild both models.
        for k, v in (saved.get("env") or {}).items():
            os.environ[k] = v
        boot()  # hot reload — rebuild both models from the fresh env
        return web.json_response(saved)
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


# ──── Saved-provider catalog handlers ─────────────────────────────────────────


async def saved_providers(_req: web.Request) -> web.Response:
    try:
        return web.json_response({"saved": providers_store.list_saved()})
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def save_provider(req: web.Request) -> web.Response:
    try:
        body = await req.json()
        saved = providers_store.save(body)
        return web.json_response(saved)
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def delete_provider(req: web.Request) -> web.Response:
    try:
        body = await req.json()
        name = (body.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        existed = providers_store.delete(name)
        if not existed:
            raise ValueError(f"provider not found: {name}")
        return web.json_response({"deleted": name})
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


# ──── Model role config handlers ──────────────────────────────────────────────


async def model_config_get(_req: web.Request) -> web.Response:
    try:
        return web.json_response(load_model_config())
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def model_config_save(req: web.Request) -> web.Response:
    try:
        body = await req.json()
        saved = save_model_config(body)
        boot()  # rebuild agents_models from the fresh config
        return web.json_response(saved)
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def history(_req: web.Request) -> web.Response:
    return web.json_response(list_runs(limit=20))


async def history_detail(req: web.Request) -> web.Response:
    """GET /history/{run_id} → full envelope JSON for one persisted run.

    S7 history-continuation: the sidebar click opens the historical report
    directly without re-running the pipeline. 404 propagates as a wire error
    so the frontend surfaces it honestly (R3-aligned: transparent errors).
    """
    run_id = req.match_info["run_id"]
    run = get_run(run_id)
    if run is None:
        err = {
            "event": "error",
            "code": "NotFound",
            "message": f"selection run not found: {run_id}",
            "status": 404,
        }
        return web.json_response(err, status=404)
    return web.json_response(run)


async def delete_all_sessions_handler(_req: web.Request) -> web.Response:
    """DELETE /sessions — wipe every session's messages (全清开关).

    The sidebar exposes this as 「清除全部记忆」 with a confirmation
    dialog; per-session clearing lives on DELETE /sessions/{id}.
    """
    sessions = list_sessions()
    total = 0
    for s in sessions:
        total += delete_session_events(session_id=s["session_id"])
    return web.Response(status=204, text=f"{total}\n")


async def list_sessions_handler(req: web.Request) -> web.Response:
    """GET /sessions — list all sessions aggregated from messages.

    the sidebar session-list source of truth. Each row has
    session_id, first/last activity, event count, run count, has_failed +
    meta (title / pinned / last_read_at).

    for the sidebar toggle; default hides them.
    """
    return web.json_response(list_sessions())


async def session_events_handler(req: web.Request) -> web.Response:
    """GET /sessions/{session_id}/events — events in sequence order.

    Default ``include_failed=False`` so the wire surface excludes error-run
    events (失败不入上下文). Pass ``?include_failed=true``
    for audit views.
    """
    session_id = req.match_info["session_id"]
    include_failed = (req.query.get("include_failed") or "").lower() == "true"
    events = list_session_events(
        session_id=session_id,
        include_failed=include_failed,
        limit=10_000,
    )
    return web.json_response(events)


async def delete_session_handler(req: web.Request) -> web.Response:
    """DELETE /sessions/{session_id} — wipe messages for a session.

    Wired to the per-session 记忆清除开关. Returns 204 No Content.
    """
    session_id = req.match_info["session_id"]
    deleted = delete_session_events(session_id=session_id)
    return web.Response(status=204, text=f"{deleted}\n")


async def session_meta_update_handler(req: web.Request) -> web.Response:
    """PATCH /sessions/{session_id} — partial update of session_meta
    (title / pinned / archived / last_read_at — sidebar
    right-click menu). UPSERT; unknown keys silently dropped server-side.

    Returns the post-update meta row so the client can refresh in one round.
    """
    try:
        body = await req.json()
        updated = update_session_meta(
            session_id=req.match_info["session_id"],
            fields=body if isinstance(body, dict) else {},
        )
        return web.json_response(updated)
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def compact_session_handler(req: web.Request) -> web.StreamResponse:
    """POST /compact {session_id, custom_instructions?} → NDJSON 流。

    Events emitted:
    - compaction_start {reason: "manual" | "threshold" | "overflow"}
    - compaction_end {reason, result?, aborted, errorMessage?}

    失败（quality 闸门拒、LLM 错、session 空）→ 写 NDJSON 错误帧，不
    落 messages 表（失败不污染 transcript 契约）。
    """
    if boot_error is not None:
        err = to_wire(boot_error)
        return web.json_response(err, status=503)

    try:
        body = await req.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return web.json_response(to_wire(e), status=400)

    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        return web.json_response(
            {"event": "error", "code": "BadRequest", "message": "session_id is required", "status": 400},
            status=400,
        )

    custom = body.get("custom_instructions")

    # settings：优先取 model_config.compaction；缺省 enabled=False（manual 触发仍可工作）
    block = load_compaction_settings(stage="selection")
    settings = CompactionSettings(
        enabled=bool(block.get("enabled", True)),  # manual 触发无视 enabled=false
        context_window=int(block.get("context_window", 32_000)),
        reserve_tokens=int(block.get("reserve_tokens", 8_192)),
        keep_recent_tokens=int(block.get("keep_recent_tokens", 4_096)),
        fallback_context_window=int(block.get("fallback_context_window", 32_000)),
        per_model_context_windows=dict(block.get("per_model_context_windows") or {}),
    )

    model = agents_models.get("selection")
    if model is None:
        return web.json_response(
            to_wire(StartupConfigError(["selection model"])),
            status=503,
        )

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/x-ndjson",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(req)
    reason = "manual"  # always manual via this endpoint
    await resp.write(
        (json.dumps({"event": "compaction_start", "reason": reason}, ensure_ascii=False) + "\n").encode("utf-8")
    )

    try:
        result = await prepare_compaction(
            session_id=session_id,
            settings=settings,
            model=model,
            custom_instructions=custom,
        )
        await resp.write(
            (
                json.dumps(
                    {
                        "event": "compaction_end",
                        "reason": reason,
                        "result": {
                            "summary": result.summary,
                            "first_kept_event_index": result.first_kept_event_index,
                            "tokens_before": result.tokens_before,
                            "estimated_tokens_after": result.estimated_tokens_after,
                        },
                        "aborted": False,
                        "willRetry": False,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
        )
    except CompactionError as e:
        await resp.write(
            (
                json.dumps(
                    {
                        "event": "compaction_end",
                        "reason": reason,
                        "result": None,
                        "aborted": False,
                        "willRetry": False,
                        "errorMessage": f"Compaction failed: {e}",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
        )
    except Exception as e:  # noqa: BLE001
        log.exception("compaction failed")
        await resp.write(
            (
                json.dumps(
                    {
                        "event": "compaction_end",
                        "reason": reason,
                        "result": None,
                        "aborted": False,
                        "willRetry": False,
                        "errorMessage": f"Compaction failed: {e}",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
        )

    await resp.write_eof()
    return resp


async def run(req: web.Request) -> web.StreamResponse:
    if boot_error is not None:
        # Surface startup error once, then close.
        resp = web.StreamResponse(
            status=503,
            headers={"Content-Type": "application/x-ndjson"},
        )
        await resp.prepare(req)
        await resp.write((json.dumps(boot_error, ensure_ascii=False) + "\n").encode("utf-8"))
        await resp.write_eof()
        return resp

    try:
        # pydantic RunBody replaces the body.get scatter read. The
        # JSON parse is folded into model_validate_json (which itself parses
        # via json.loads); unknown fields surface as 400 (root cause)
        # rather than being silently dropped. Field names match the current
        # wire surface so existing clients and scripts do not break.
        body = RunBody.model_validate_json(await req.text())
    except ValidationError as e:
        return await _validation_400(req, e)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # UnicodeDecodeError: non-UTF-8 request bodies (e.g. a GBK-encoded
        # client) must surface as a wire-format 400, not a raw aiohttp 500.
        resp = web.StreamResponse(
            status=400,
            headers={"Content-Type": "application/x-ndjson"},
        )
        await resp.prepare(req)
        await resp.write(
            (json.dumps(to_wire(e), ensure_ascii=False) + "\n").encode("utf-8")
        )
        await resp.write_eof()
        return resp

    query = (body.query or "").strip()
    if not query:
        err = {
            "event": "error",
            "code": "BadRequest",
            "message": "query is required",
            "status": 400,
        }
        resp = web.StreamResponse(
            status=400,
            headers={"Content-Type": "application/x-ndjson"},
        )
        await resp.prepare(req)
        await resp.write((json.dumps(err, ensure_ascii=False) + "\n").encode("utf-8"))
        await resp.write_eof()
        return resp

    # follow-up channel: prior run's envelope renders into prose
    # context and is prepended to the intent prompt. Bad historyRunId fails
    # closed as a 404 wire error (enthusiast get_object_or_404 pattern) so
    # the frontend surfaces the honest failure rather than silently dropping.
    #
    # When no explicit historyRunId is supplied but session_id
    # is present, resolve the latest successful run of this session.
    # Kills the "frontend must send correct run id" bug at the structural
    # level: the wire only carries session_id; the server is the single
    # source of truth for history.
    history_context = ""
    history_run_id = body.historyRunId
    if history_run_id:
        prior = get_run(history_run_id)
        if prior is None:
            err = {
                "event": "error",
                "code": "NotFound",
                "message": f"history run not found: {history_run_id}",
                "status": 404,
            }
            resp = web.StreamResponse(
                status=404,
                headers={"Content-Type": "application/x-ndjson"},
            )
            await resp.prepare(req)
            await resp.write((json.dumps(err, ensure_ascii=False) + "\n").encode("utf-8"))
            await resp.write_eof()
            return resp
        history_context = render_history_context(prior, created_at=prior.get("created_at"))
    elif body.session_id:
        prior = _resolve_session_latest_run(body.session_id)
        if prior is not None:
            history_context = render_history_context(prior, created_at=prior.get("created_at"))

    session_id = body.session_id or str(uuid.uuid4())
    # per-invocation run_id (distinct from session_id which links
    # multiple runs; one session can hold many runs over time).
    run_id = str(uuid.uuid4())
    observability.set_run_meta(session_id=session_id, run_id=run_id)

    # replay contract: save the user message as sequence=0
    # BEFORE the loop starts. The replay filter splits user_message from
    # wire events and uses its payload to seed the user bubble. Without
    # this, the sidebar session list has no way to reconstruct what the
    # user originally asked.
    try:
        save_event(
            session_id=session_id,
            run_id=run_id,
            event_type="user_message",
            sequence=0,
            payload={
                "query": query,
                "history_run_id": history_run_id,
            },
        )
    except Exception:  # noqa: BLE001
        log.exception("save user_message failed (non-fatal)")

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/x-ndjson",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(req)

    # The `empty` event is emitted by the loop's search tool when it finds
    # zero candidates (single source of truth — no summary string matching).

    if durable.enabled():
        # durable-agent-tasks — single-executor tee: workflow owns the driver
        # execution + persistence; this handler only streams the tee queue.
        durable.launch_run(
            "selection", session_id, run_id,
            {"query": query, "history_context": history_context,
             "session_id": session_id, "run_id": run_id},
        )
        try:
            async for event in durable.stream_events(run_id):
                wire_line = (
                    json.dumps(event, ensure_ascii=False, default=str) + "\n"
                ).encode("utf-8")
                await resp.write(wire_line)
        except (ConnectionResetError, ConnectionError):
            durable.abort(run_id)
            log.info("client disconnected; durable run aborted")
        return resp

    disconnected = False
    try:
        if not agents_models.get("selection"):
            raise StartupConfigError(["selection model"])
        # Wire events start at sequence=1; sequence=0 is reserved for the
        # user_message saved above (replay contract).
        sequence = 1
        failed = False
        final_event: dict | None = None
        stream = stream_run_loop(
            query=query,
            session_id=session_id,
            run_id=run_id,
            history_context=history_context,
            agents_models=agents_models,
        )
        # aclosing — a client disconnect mid-stream must close the async
        # generator deterministically (the generator's finally cancels the
        # in-flight agent task; without it the GeneratorExit would only fire
        # at GC, if ever).
        async with aclosing(stream):
            async for event in stream:
                # U+FFFD detection at the stream-loop throat
                # (save+emit share the same gate, so the wire and the DB stay
                # in lock-step). HTTP-layer fatal decode already blocks GBK at
                # the boundary; what remains is upstream provider output
                # (LLM streaming text with replacement chars). Saving the
                # original event would corrupt replay (contract) — so
                # the original payload is dropped and a structured error event
                # is persisted with answer_failed=1 (per : failure rows
                # are persisted-visible but excluded from later context injection).
                line = (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode("utf-8")
                if "\ufffd" in line.decode("utf-8"):
                    failed = True
                    bad_event = {
                        "event": "error",
                        "code": "EncodingError",
                        "message": (
                            f"output contains U+FFFD replacement char at "
                            f"sequence {sequence} (provider upstream lossy "
                            f"decode); original event dropped"
                        ),
                        "session_id": session_id,
                        "run_id": run_id,
                    }
                    bad_line = (json.dumps(bad_event, ensure_ascii=False) + "\n").encode("utf-8")
                    try:
                        save_event(
                            session_id=session_id,
                            run_id=run_id,
                            event_type="error",
                            sequence=sequence,
                            payload=bad_event,
                            answer_failed=failed,
                        )
                    except Exception:  # noqa: BLE001
                        log.exception("save_event failed (non-fatal)")
                    sequence += 1
                    await resp.write(bad_line)
                    continue
                # persist every event into messages (typed rows).
                # Errors are persisted-but-not-injected (+  contract);
                # downstream ones of list_session_events(... include_failed=False) filters
                # them out of context injection. save_event failures are logged
                # and never abort the wire stream (persistence is best-effort).
                ev_type = event.get("event", "unknown")
                if ev_type == "error":
                    failed = True
                if ev_type == "final":
                    final_event = event
                try:
                    save_event(
                        session_id=session_id,
                        run_id=run_id,
                        event_type=ev_type,
                        sequence=sequence,
                        payload=event,
                        answer_failed=failed,
                    )
                except Exception:  # noqa: BLE001
                    log.exception("save_event failed (non-fatal)")
                sequence += 1
                await resp.write(line)

        # the loop runtime has no
        # persist node; the throat mirrors the legacy persist node's
        # save_run contract (same report envelope shape:
        # report/candidates/intent) and then deterministically
        # materializes memory-graph edges (zero LLM). Best-effort —
        # never breaks the wire stream.
        if final_event is not None and not failed:
            report = (
                final_event.get("report")
                if isinstance(final_event.get("report"), dict)
                else {}
            )
            candidates = final_event.get("candidates") or []
            envelope = {
                "report": report,
                "candidates": candidates,
                "intent": None,
            }
            seed_keyword = str(report.get("seed_keyword") or "")
            try:
                save_run(
                    query=query,
                    seed_keyword=seed_keyword,
                    decision=str(final_event.get("decision") or ""),
                    report=envelope,
                    run_id=run_id,
                )
                materialize_edges_for_run(
                    run_id=run_id,
                    session_id=session_id,
                    keyword=seed_keyword,
                    decision=str(final_event.get("decision") or ""),
                    report=envelope,
                    candidates=candidates,
                )
            except Exception:  # noqa: BLE001
                log.exception("loop-run persist/materialize failed (non-fatal)")

            # fire-and-forget auto title
            # generation. Only writes if the user has not already named the
            # session (manual rename wins per design D2). All errors
            # swallowed: a failed title never breaks the run.
            try:
                current_meta = get_session_meta(session_id=session_id)
                if not current_meta.get("title"):
                    model = agents_models.get("selection")
                    if model is not None:
                        _spawn_title_task(model, query, session_id)
            except Exception:  # noqa: BLE001
                log.exception("title spawn check failed (non-fatal)")
    except (ConnectionResetError, ConnectionError):
        # client went away mid-stream (browser 停止 / Next abort / kill):
        # there is no response left to write an error frame to. The aclosing
        # above has already closed the generator, whose finally cancelled the
        # agent task — nothing left to clean up here beyond the log line.
        disconnected = True
        log.info("client disconnected; run aborted")
    except Exception as e:  # noqa: BLE001
        log.exception("run failed")
        await resp.write(
            (json.dumps(to_wire(e), ensure_ascii=False) + "\n").encode("utf-8")
        )

    if not disconnected:
        await resp.write_eof()
    return resp


async def listing_run(req: web.Request) -> web.StreamResponse:
    """NDJSON stream for one listing draft run .

    Body: {"candidate": {ProductCandidate fields}, "marketCode"?: "US",
           "brand"?: str, "competitorBrands"?: [str], "session_id"?: str}
    """
    if boot_error is not None:
        resp = web.StreamResponse(
            status=503,
            headers={"Content-Type": "application/x-ndjson"},
        )
        await resp.prepare(req)
        await resp.write((json.dumps(boot_error, ensure_ascii=False) + "\n").encode("utf-8"))
        await resp.write_eof()
        return resp

    try:
        # ListingRunBody enforces the wire contract (extra="forbid").
        # root cause: camel/snake mismatch used to silently fall back to a
        # fresh UUID, breaking session aggregation.
        body = ListingRunBody.model_validate_json(await req.text())
    except ValidationError as e:
        return await _validation_400(req, e)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        resp = web.StreamResponse(
            status=400,
            headers={"Content-Type": "application/x-ndjson"},
        )
        await resp.prepare(req)
        await resp.write(
            (json.dumps(to_wire(e), ensure_ascii=False) + "\n").encode("utf-8")
        )
        await resp.write_eof()
        return resp

    candidate_raw = body.candidate
    if not candidate_raw:
        err = {
            "event": "error",
            "code": "BadRequest",
            "message": "candidate is required",
            "status": 400,
        }
        resp = web.StreamResponse(
            status=400,
            headers={"Content-Type": "application/x-ndjson"},
        )
        await resp.prepare(req)
        await resp.write((json.dumps(err, ensure_ascii=False) + "\n").encode("utf-8"))
        await resp.write_eof()
        return resp

    try:
        candidate = ProductCandidate.model_validate(candidate_raw)
    except ValidationError as e:
        err = {
            "event": "error",
            "code": "BadRequest",
            "message": f"invalid candidate: {e}",
            "status": 400,
        }
        resp = web.StreamResponse(
            status=400,
            headers={"Content-Type": "application/x-ndjson"},
        )
        await resp.prepare(req)
        await resp.write((json.dumps(err, ensure_ascii=False) + "\n").encode("utf-8"))
        await resp.write_eof()
        return resp

    #  mirror the selection run's persistence contract: the
    # run_id is generated server-side BEFORE streaming so every wire event
    # (and the user_message row below) lands under one id in ``messages``;
    # the listing throat then reuses it for the ``listing_runs`` row.
    session_id = body.session_id or str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    observability.set_run_meta(session_id=session_id, run_id=run_id)

    # Replay contract (shape, listing variant): sequence=0 records the
    # trigger turn. ``kind: "listing_turn"`` is the discriminator the frontend
    # replay uses to fold this run via applyListingEvent instead of applyEvent;
    # candidate/brand are carried so replay can rebuild ListingRunState.
    label = candidate.name_en or candidate.name_cn
    try:
        save_event(
            session_id=session_id,
            run_id=run_id,
            event_type="user_message",
            sequence=0,
            payload={
                "kind": "listing_turn",
                "query": f"为「{label}」生成 Listing",
                "candidate": candidate.model_dump(),
                "market_code": body.marketCode or "US",
                "brand": (body.brand or "").strip(),
            },
        )
    except Exception:  # noqa: BLE001
        log.exception("save listing user_message failed (non-fatal)")

    # listing sessions get a deterministic
    # title at trigger time (zero LLM). Idempotent: skips if the user has
    # already named the session.
    try:
        if not get_session_meta(session_id=session_id).get("title"):
            update_session_meta(
                session_id=session_id,
                fields={"title": f"Listing · {label}"[:32]},
            )
    except Exception:  # noqa: BLE001
        log.exception("listing title write failed (non-fatal)")

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/x-ndjson",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(req)

    if durable.enabled():
        durable.launch_run(
            "listing", session_id, run_id,
            {"candidate": candidate_raw,
             "market_code": body.marketCode or "US",
             "brand": (body.brand or "").strip(),
             "competitor_brands": body.competitorBrands or [],
             "session_id": session_id, "run_id": run_id},
        )
        try:
            async for event in durable.stream_events(run_id):
                wire_line = (
                    json.dumps(event, ensure_ascii=False, default=str) + "\n"
                ).encode("utf-8")
                await resp.write(wire_line)
        except (ConnectionResetError, ConnectionError):
            durable.abort(run_id)
            log.info("client disconnected; durable listing aborted")
        return resp

    sequence = 1
    final_listing: dict | None = None
    disconnected = False
    try:
        if not agents_models.get("listing"):
            raise StartupConfigError(["listing model"])
        stream = stream_run_listing_loop(
            candidate=candidate,
            market_code=body.marketCode or "US",
            brand=(body.brand or "").strip(),
            competitor_brands=body.competitorBrands or [],
            session_id=session_id,
            run_id=run_id,
            agents_models=agents_models,
        )
        # aclosing — same disconnect contract as run: closing the generator
        # deterministically is what triggers its task-cancel finally.
        async with aclosing(stream):
            async for event in stream:
                line = (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode("utf-8")
                # Same best-effort throat as the selection run: every event is
                # persisted under (session_id, run_id, sequence) so reopening the
                # task replays the listing turn; failures never break the wire.
                ev_type = event.get("event", "unknown")
                if ev_type == "final" and event.get("kind") == "listing":
                    final_listing = event
                try:
                    save_event(
                        session_id=session_id,
                        run_id=run_id,
                        event_type=ev_type,
                        sequence=sequence,
                        payload=event,
                        answer_failed=ev_type == "error",
                    )
                except Exception:  # noqa: BLE001
                    log.exception("save_event failed (non-fatal)")
                sequence += 1
                await resp.write(line)

        # the listing loop runtime has no save node; the throat
        # mirrors the legacy save node's envelope (listing/issues/hard_failed
        # + the candidate row, which the export CLI reads for price prefill).
        # Best-effort — never breaks the wire stream.
        if final_listing is not None:
            try:
                save_listing_run(
                    session_id=session_id,
                    product_id=str(final_listing.get("product_id") or ""),
                    market_code=str(final_listing.get("market_code") or "US"),
                    listing={
                        "listing": final_listing.get("listing"),
                        "issues": final_listing.get("issues") or [],
                        "hard_failed": bool(final_listing.get("hard_failed")),
                        "candidate": candidate.model_dump(),
                    },
                    run_id=run_id,
                )
            except Exception:  # noqa: BLE001
                log.exception("loop-listing save_listing_run failed (non-fatal)")
    except (ConnectionResetError, ConnectionError):
        # client disconnected mid-draft: no error frame to write, the
        # generator's finally (via aclosing) already cancelled the agent task.
        disconnected = True
        log.info("client disconnected; run aborted")
    except Exception as e:  # noqa: BLE001
        log.exception("listing run failed")
        err_event = to_wire(e)
        try:
            save_event(
                session_id=session_id,
                run_id=run_id,
                event_type="error",
                sequence=sequence,
                payload=err_event,
                answer_failed=True,
            )
        except Exception:  # noqa: BLE001
            log.exception("save error event failed (non-fatal)")
        await resp.write(
            (json.dumps(err_event, ensure_ascii=False) + "\n").encode("utf-8")
        )

    if not disconnected:
        await resp.write_eof()
    return resp


async def chat_run(req: web.Request) -> web.StreamResponse:
    """general-agent-chat — NDJSON stream for one general-agent chat turn.

    Mirrors the /run throat (user_message at sequence=0 → per-event U+FFFD
    gate + save_event → disconnect-cancel contract) with two deliberate
    differences: the user_message payload carries ``kind:"chat_turn"`` (the
    replay discriminator, listing_turn precedent), and the chat run itself
    never writes selection_runs — a conversation is not a selection run.
    Nested selection/listing pipelines persist through the domain tools'
    hooks inside stream_general_loop.
    """
    if boot_error is not None:
        resp = web.StreamResponse(
            status=503,
            headers={"Content-Type": "application/x-ndjson"},
        )
        await resp.prepare(req)
        await resp.write((json.dumps(boot_error, ensure_ascii=False) + "\n").encode("utf-8"))
        await resp.write_eof()
        return resp

    try:
        body = ChatBody.model_validate_json(await req.text())
    except ValidationError as e:
        return await _validation_400(req, e)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Same GBK-boundary contract as /run: non-UTF-8 bodies surface as a
        # wire-format 400, never a raw aiohttp 500.
        return await _bad_request_ndjson(req, "request body is not valid UTF-8")

    query = (body.query or "").strip()
    if not query:
        return await _bad_request_ndjson(req, "query is required")

    session_id = body.session_id or str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    observability.set_run_meta(session_id=session_id, run_id=run_id)

    # conversation transcript from persisted session events (oldest
    # last). No prior events (new session) → empty context.
    history_context = ""
    if body.session_id:
        prior_events = list_session_events(
            session_id=session_id, include_failed=False, limit=200
        )
        history_context = _chat_history_context(prior_events)

    # Replay contract — sequence=0 user_message with the chat_turn
    # discriminator; replaySessionEvents routes it to the chat fold path.
    try:
        save_event(
            session_id=session_id,
            run_id=run_id,
            event_type="user_message",
            sequence=0,
            payload={"query": query, "kind": "chat_turn"},
        )
    except Exception:  # noqa: BLE001
        log.exception("save user_message failed (non-fatal)")

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/x-ndjson",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(req)

    if durable.enabled():
        durable.launch_run(
            "chat", session_id, run_id,
            {"query": query, "history_context": history_context,
             "session_id": session_id, "run_id": run_id},
        )
        try:
            async for event in durable.stream_events(run_id):
                wire_line = (
                    json.dumps(event, ensure_ascii=False, default=str) + "\n"
                ).encode("utf-8")
                await resp.write(wire_line)
        except (ConnectionResetError, ConnectionError):
            durable.abort(run_id)
            log.info("client disconnected; durable chat aborted")
        return resp

    disconnected = False
    try:
        if not agents_models.get("selection"):
            raise StartupConfigError(["selection model"])
        sequence = 1
        failed = False
        stream = stream_general_loop(
            query=query,
            session_id=session_id,
            run_id=run_id,
            history_context=history_context,
            agents_models=agents_models,
        )
        # aclosing — client disconnect closes the generator, whose finally
        # cancels the agent task (and any in-flight nested pipeline).
        async with aclosing(stream):
            async for event in stream:
                line = (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode("utf-8")
                # Same U+FFFD stream-loop-throat gate as /run: provider-side
                # lossy decode must not corrupt the wire or the replay log.
                if "\ufffd" in line.decode("utf-8"):
                    failed = True
                    bad_event = {
                        "event": "error",
                        "code": "EncodingError",
                        "message": (
                            f"output contains U+FFFD replacement char at "
                            f"sequence {sequence} (provider upstream lossy "
                            f"decode); original event dropped"
                        ),
                        "session_id": session_id,
                        "run_id": run_id,
                    }
                    bad_line = (json.dumps(bad_event, ensure_ascii=False) + "\n").encode("utf-8")
                    try:
                        save_event(
                            session_id=session_id,
                            run_id=run_id,
                            event_type="error",
                            sequence=sequence,
                            payload=bad_event,
                            answer_failed=True,
                        )
                    except Exception:  # noqa: BLE001
                        log.exception("save_event failed (non-fatal)")
                    sequence += 1
                    await resp.write(bad_line)
                    continue
                ev_type = event.get("event", "unknown")
                if ev_type == "error":
                    failed = True
                try:
                    save_event(
                        session_id=session_id,
                        run_id=run_id,
                        event_type=ev_type,
                        sequence=sequence,
                        payload=event,
                        answer_failed=failed,
                    )
                except Exception:  # noqa: BLE001
                    log.exception("save_event failed (non-fatal)")
                sequence += 1
                await resp.write(line)
        # Deliberate: no save_run / materialize here — chat runs are not
        # selection runs (selection_runs stays pure for /history, follow-up
        # context resolution, and the memory graph).

        # fire-and-forget auto title, same
        # contract as /run: only when still untitled (manual rename wins,
        # in-flight write re-checked inside the task), all errors swallowed.
        try:
            current_meta = get_session_meta(session_id=session_id)
            if not current_meta.get("title"):
                model = agents_models.get("selection")
                if model is not None:
                    _spawn_title_task(model, query, session_id)
        except Exception:  # noqa: BLE001
            log.exception("title spawn check failed (non-fatal)")
    except (ConnectionResetError, ConnectionError):
        disconnected = True
        log.info("client disconnected; chat run aborted")
    except Exception as e:  # noqa: BLE001
        log.exception("chat run failed")
        await resp.write(
            (json.dumps(to_wire(e), ensure_ascii=False) + "\n").encode("utf-8")
        )

    if not disconnected:
        await resp.write_eof()
    return resp


# ──── Listing dual-channel handlers  ───────────────────────────────


def _facts_overrides(body: dict) -> dict:
    overrides: dict = {}
    if body.get("price_usd") is not None:
        overrides["price_usd"] = float(body["price_usd"])
    if body.get("quantity") is not None:
        overrides["quantity"] = int(body["quantity"])
    if body.get("sku") is not None:
        overrides["sku"] = str(body["sku"]).strip()
    if body.get("brand") is not None:
        overrides["brand"] = str(body["brand"]).strip()
    if body.get("product_type") is not None:
        overrides["product_type"] = str(body["product_type"]).strip()
    return overrides


def _load_run_or_raise(run_id: str) -> dict:
    from .persistence import get_listing_run

    run = get_listing_run(run_id or "")
    if run is None:
        raise ValueError(f"listing run not found: {run_id}")
    return run


async def listing_export(req: web.Request) -> web.Response:
    """POST {run_id, price_usd?, quantity?, sku?, brand?} → 标准导出文件。"""
    try:
        body = await req.json()
        run = _load_run_or_raise(str(body.get("run_id") or ""))
        export = render_export(run, facts_overrides=_facts_overrides(body))
        return web.json_response(export)
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def listing_channel(_req: web.Request) -> web.Response:
    try:
        return web.json_response(
            {"channel": resolve_upload_channel(load_spapi_creds())}
        )
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def listing_runs_list(_req: web.Request) -> web.Response:
    """GET /listing/runs — summaries of persisted drafts ( 列表视图)."""
    try:
        return web.json_response(list_listing_runs())
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def listing_run_detail(req: web.Request) -> web.Response:
    """GET /listing/runs/{run_id} — full draft envelope for the read-only view.

    404 as a wire error for unknown ids, matching the /history/{run_id}
    contract (R3-aligned transparent errors)."""
    run_id = req.match_info.get("run_id", "")
    run = get_listing_run(run_id)
    if run is None:
        return web.json_response(
            {
                "event": "error",
                "code": "NotFound",
                "message": f"listing run not found: {run_id}",
                "status": 404,
            },
            status=404,
        )
    return web.json_response(run)


async def listing_run_patch(req: web.Request) -> web.Response:
    """PATCH /listing/runs/{run_id}

    Body: ``{"title": "..."}``. Empty/whitespace clears the alias (NULL).
    Returns the post-update summary row; 404 if no such run.
    """
    run_id = req.match_info.get("run_id", "")
    try:
        body = await req.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # non-UTF-8 request bodies must surface as a wire-format 400
        # so a GBK-encoded client never reaches the persistence layer as
        # raw bytes (silent U+FFFD corruption bug).
        return web.json_response(
            {
                "event": "error",
                "code": "BadRequest",
                "message": f"invalid JSON body: {e}",
                "status": 400,
            },
            status=400,
        )
    if not isinstance(body, dict):
        return _bad_request_wire("body must be a JSON object")
    if "title" not in body:
        return _bad_request_wire("title is required")
    title = body["title"]
    if not isinstance(title, str):
        return _bad_request_wire("title must be a string")
    if len(title) > 64:
        return _bad_request_wire("title is too long (max 64 chars)")
    try:
        updated = rename_listing_run(run_id=run_id, title=title)
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)
    if updated is None:
        return web.json_response(
            {
                "event": "error",
                "code": "NotFound",
                "message": f"listing run not found: {run_id}",
                "status": 404,
            },
            status=404,
        )
    return web.json_response(updated)


async def listing_run_delete(req: web.Request) -> web.Response:
    """DELETE /listing/runs/{run_id}

    Hard delete (drafts are reproducible from the candidate).
    204 on success, 404 if no such run — mirrors the session DELETE
    contract for delete_session_handler.
    """
    run_id = req.match_info.get("run_id", "")
    try:
        removed = delete_listing_run(run_id=run_id)
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)
    if not removed:
        return web.json_response(
            {
                "event": "error",
                "code": "NotFound",
                "message": f"listing run not found: {run_id}",
                "status": 404,
            },
            status=404,
        )
    return web.Response(status=204, text="")


# ──── feature-wave-1:Listing 人工编辑直写 + 绘蛙图生成 ─────────────────────

class ListingFieldsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: dict[str, Any]


_LISTING_FIELD_WHITELIST = {
    "item_name", "bullet_point", "product_description", "generic_keyword",
    "title_en", "bullets_en", "description_en", "title_jp", "bullets_jp",
    "description_jp", "image_url",
}

async def listing_patch_fields_handler(req: web.Request) -> web.Response:
    """PATCH /listing/runs/{run_id}/fields — 人工编辑直写(白名单字段)。"""
    run_id = req.match_info["run_id"]
    try:
        body = ListingFieldsPatch.model_validate_json(await req.text())
    except ValidationError as e:
        return await _validation_400(req, e)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response(
            {"error": "EncodingError", "message": "invalid utf-8 body", "status": 400}, status=400)
    bad = [k for k in body.fields if k not in _LISTING_FIELD_WHITELIST]
    if bad:
        return web.json_response(
            {"error": "ForbiddenField", "message": f"fields not allowed: {bad}", "status": 400}, status=400)
    try:
        for k, v in body.fields.items():
            update_listing_field(run_id, k, v)
        return web.json_response({"ok": True})
    except ValueError as e:
        return web.json_response({"error": str(e), "status": 404}, status=404)


async def listing_gen_image_handler(req: web.Request) -> web.Response:
    """POST /listing/gen-image — 手动触发绘蛙图生成(镜像 regen 校验)。"""
    from .agents.image_tool import get_image_provider
    provider = get_image_provider()
    if provider is None:
        return web.json_response(
            {"error": "ImageProviderUnavailable", "message": "HUIWA_API_KEY not set", "status": 503}, status=503)
    try:
        body = await req.json()
        description = str(body.get("description") or "").strip()
        if not description:
            raise ValueError("description is required")
        out = await provider.generate(description)
        return web.json_response({"ok": True, "url": out["url"]})
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)

# ──── durable-agent-tasks：审批端点 ──────────────────────────────────────────

async def approval_resolve_handler(req: web.Request) -> web.Response:
    """POST /runs/{run_id}/approval {decision: approve|reject} — durable 审批决议。"""
    try:
        body = await req.json()
        decision = str(body.get("decision") or "")
        if decision not in ("approve", "reject"):
            return web.json_response(
                {"error": "ValidationError", "message": "decision must be approve|reject"},
                status=400,
            )
        run_id = req.match_info["run_id"]
        durable.resolve_approval(run_id, decision)
        return web.json_response({"ok": True, "decision": decision})
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def approvals_list_handler(req: web.Request) -> web.Response:
    """GET /runs/approvals?status=pending|resolved|all — 审批清单。"""
    try:
        status = req.query.get("status", "pending")
        if status not in ("pending", "resolved", "all"):
            status = "pending"
        return web.json_response(list_approvals(status=status))
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)

# ──── Preferences (偏好 RAG user CRUD) ───────────────────────────


async def preferences_list(_req: web.Request) -> web.Response:
    """GET /preferences — list active preferences (metadata only, no vector)."""
    try:
        return web.json_response(list_active_preferences())
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def preferences_add(req: web.Request) -> web.Response:
    """POST /preferences {text, category?} — embed via mock_embed (deterministic,
    no aimux dependency) and persist. The RAG retrieval path will use this
    vector for KNN similarity scoring."""
    try:
        body = await req.json()
        text = (body.get("text") or "").strip()
        category = (body.get("category") or "user-added").strip() or "user-added"
        if not text:
            raise ValueError("text is required")
        if os.environ.get("DATABASE_URL") and os.environ.get("EMBEDDING_MODEL"):
            # postgres-data-platform — 写入侧真实 embedding(aimux 向量模型)
            embedding = await real_embed(
                text,
                provider=os.environ.get("EMBEDDING_PROVIDER", "openai"),
                model=os.environ["EMBEDDING_MODEL"],
                api_key=os.environ.get("LLM_API_KEY"),
                base_url=os.environ.get("LLM_BASE_URL") or None,
                dimensions=int(os.environ.get("EMBEDDING_DIM", "1536")),
            )
            _dim = int(os.environ.get("EMBEDDING_DIM", "1536"))
            if len(embedding) != _dim:
                raise StartupConfigError(
                    [f"EMBEDDING_DIM (model returned {len(embedding)} != {_dim})"]
                )
        else:
            embedding = mock_embed(text)
        pref_id = insert_preference(
            category=category,
            preference_text=text,
            embedding=embedding,
            source_session_id="manual",
            source_run_id="manual",
        )
        return web.json_response({"id": pref_id})
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def preferences_delete(req: web.Request) -> web.Response:
    """DELETE /preferences/{id} — soft-delete (active=0, no superseder).
    Distinct from supersede_preference (which chains old→new)."""
    try:
        pref_id = int(req.match_info.get("id", "0"))
        rows = delete_preference(preference_id=pref_id)
        if rows == 0:
            return web.json_response(
                {
                    "event": "error",
                    "code": "NotFound",
                    "message": f"preference not found or already inactive: {pref_id}",
                    "status": 404,
                },
                status=404,
            )
        return web.json_response({"id": pref_id, "deleted": True})
    except ValueError as e:
        return _wire_error_response(e)
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def preferences_patch(req: web.Request) -> web.Response:
    """PATCH /preferences/{id} {text?, category?} — edit an active preference.
    When text changes the server re-embeds and rewrites the vector (sqlite-vec
    or pgvector depending on DATABASE_URL). Empty text is rejected — use
    DELETE to remove, this is the edit path."""
    try:
        pref_id = int(req.match_info.get("id", "0"))
        body = await req.json()
        new_text = body.get("text")
        new_category = body.get("category")
        if new_text is not None:
            if not isinstance(new_text, str) or not new_text.strip():
                return web.json_response(
                    {
                        "event": "error",
                        "code": "BadRequest",
                        "message": "text must be a non-empty string",
                        "status": 400,
                    },
                    status=400,
                )
            new_text = new_text.strip()
        if new_category is not None and not isinstance(new_category, str):
            return web.json_response(
                {
                    "event": "error",
                    "code": "BadRequest",
                    "message": "category must be a string",
                    "status": 400,
                },
                status=400,
            )

        embedding: list[float] | None = None
        if new_text is not None:
            if os.environ.get("DATABASE_URL") and os.environ.get("EMBEDDING_MODEL"):
                embedding = await real_embed(
                    new_text,
                    provider=os.environ.get("EMBEDDING_PROVIDER", "openai"),
                    model=os.environ["EMBEDDING_MODEL"],
                    api_key=os.environ.get("LLM_API_KEY"),
                    base_url=os.environ.get("LLM_BASE_URL") or None,
                    dimensions=int(os.environ.get("EMBEDDING_DIM", "1536")),
                )
                _dim = int(os.environ.get("EMBEDDING_DIM", "1536"))
                if len(embedding) != _dim:
                    raise StartupConfigError(
                        [f"EMBEDDING_DIM (model returned {len(embedding)} != {_dim})"]
                    )
            else:
                embedding = mock_embed(new_text)

        rows = update_preference(
            preference_id=pref_id,
            preference_text=new_text,
            category=new_category,
            embedding=embedding,
        )
        if rows == 0:
            return web.json_response(
                {
                    "event": "error",
                    "code": "NotFound",
                    "message": f"preference not found or already inactive: {pref_id}",
                    "status": 404,
                },
                status=404,
            )
        return web.json_response({"id": pref_id, "updated": True})
    except ValueError as e:
        return _wire_error_response(e)
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def listing_upload(req: web.Request) -> web.Response:
    """POST {run_id, sku, price_usd?, ...} → SP-API PUT（无凭据 → 409 引导导出）。"""
    try:
        body = await req.json()
        creds = load_spapi_creds()
        if resolve_upload_channel(creds) != "api":
            return web.json_response(
                {
                    "event": "error",
                    "code": "NoSpapiCredentials",
                    "message": "未配置 SP-API 凭据——请在配置页填写，或改用导出通道",
                    "status": 409,
                },
                status=409,
            )
        run = _load_run_or_raise(str(body.get("run_id") or ""))
        export = render_export(run, facts_overrides=_facts_overrides(body))
        facts = ListingFacts.model_validate(export["facts"])
        if not facts.sku:
            raise ValueError("sku is required for upload")
        client = SpapiClient(creds)
        result = await client.put_listing(facts.sku, export["payload"])
        return web.json_response(
            {"status": result["status"], "body": result["body"]}
        )
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def listing_regen_field(req: web.Request) -> web.Response:
    """R8 single-field regen — POST {run_id, field_name} → re-generate one
    field via the listing model, re-validate, persist the patch. Other
    fields are untouched. Field name whitelist enforced (is_regen_field);
    unknown field → 400 wire.
    """
    try:
        body = await req.json()
        run_id = str(body.get("run_id") or "")
        field_name = str(body.get("field_name") or "")
        if not run_id or not field_name:
            raise ValueError("run_id and field_name are required")
        if not is_regen_field(field_name):
            raise ValueError(f"unsupported field_name: {field_name}")
        model = agents_models.get("listing")
        if model is None:
            raise StartupConfigError(["listing model"])
        run = _load_run_or_raise(run_id)
        draft = ListingOutput.model_validate(run["listing"])
        market = load_market_config(code=run.get("market_code") or "US")
        new_draft, issues, hard_failed = await regenerate_listing_field(
            model, draft, field_name, market,
        )
        update_listing_field(run_id, field_name, getattr(new_draft, field_name))
        return web.json_response(
            {
                "field": field_name,
                "value": getattr(new_draft, field_name),
                "issues": [i.model_dump() for i in issues],
                "hard_failed": hard_failed,
            }
        )
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def spapi_config_get(_req: web.Request) -> web.Response:
    try:
        return web.json_response(load_spapi_creds())
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


async def spapi_config_save(req: web.Request) -> web.Response:
    try:
        body = await req.json()
        saved = save_spapi_creds(body)
        return web.json_response(saved)
    except Exception as e:  # noqa: BLE001
        return _wire_error_response(e)


# ──── App factory ────────────────────────────────────────────────────────────


# ──── local-source guard (Host/Origin pinning) ─────────────────────────

# The server binds 127.0.0.1 only, but binding alone doesn't stop a malicious
# web page from driving cross-site "simple requests" (text/plain, no
# preflight) at http://127.0.0.1:<port>/* — which could rewrite config/save's
# base_url (LLM traffic hijack), burn /run quota, or wipe sessions. The nonce
# handshake only guards /healthz adoption, not these. Rules (fail-closed):
# ① Host must be a loopback hostname (any port); ② when an Origin header is
# present, its host must be loopback too; ③ an Origin that fails to parse as
# http(s) — e.g. a sandboxed iframe's literal "null" — is rejected as well.
# The Node bridge and curl send no Origin at all → rule ① alone admits them
# (Node v24 undici fetch POST sends no Origin, verified 2026-09-05).
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
LOCAL_ORIGIN_HOSTNAMES = {"127.0.0.1", "localhost"}


def _hostname_of(authority: str) -> str:
    """Hostname from a Host/Origin authority, port stripped (IPv6
    bracket-aware; a bare multi-colon host is already IPv6 — keep whole)."""
    host = (authority or "").strip()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host
    if host.count(":") > 1:
        return host
    return host.rsplit(":", 1)[0]


def _origin_hostname(origin: str) -> str | None:
    """Hostname of an Origin header; None when it is not a parseable http(s)
    origin (the literal "null", opaque origins, garbage)."""
    raw = (origin or "").strip()
    if not raw or "://" not in raw:
        return None
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    return (parts.hostname or "").lower() or None


def _forbidden_source(message: str) -> web.Response:
    return web.json_response(
        {"event": "error", "code": "ForbiddenSource", "message": message, "status": 403},
        status=403,
    )


@web.middleware
async def local_source_guard(request: web.Request, handler: Any) -> web.StreamResponse:
    host = _hostname_of(request.headers.get("Host", "")).lower()
    if host not in LOCAL_HOSTNAMES:
        return _forbidden_source(f"Host not allowed: {request.headers.get('Host', '')}")
    origin = request.headers.get("Origin")
    if origin:
        origin_host = _origin_hostname(origin)
        if origin_host is None or origin_host not in LOCAL_ORIGIN_HOSTNAMES:
            return _forbidden_source(f"Origin not allowed: {origin}")
    return await handler(request)


async def _on_final_selection(*, session_id: str, run_id: str, payload: dict[str, Any], final_event: dict | None, failed: bool) -> None:
    if final_event is None or failed:
        return
    report = final_event.get("report") if isinstance(final_event.get("report"), dict) else {}
    candidates = final_event.get("candidates") or []
    envelope = {"report": report, "candidates": candidates, "intent": None}
    seed_keyword = str(report.get("seed_keyword") or "")
    try:
        save_run(
            query=payload.get("query") or "",
            seed_keyword=seed_keyword,
            decision=str(final_event.get("decision") or ""),
            report=envelope,
            run_id=run_id,
        )
        materialize_edges_for_run(
            run_id=run_id,
            session_id=session_id,
            keyword=seed_keyword,
            decision=str(final_event.get("decision") or ""),
            report=envelope,
            candidates=candidates,
        )
    except Exception:
        log.exception("durable loop-run persist/materialize failed (non-fatal)")
    try:
        current_meta = get_session_meta(session_id=session_id)
        if not current_meta.get("title"):
            model = agents_models.get("selection")
            if model is not None:
                _spawn_title_task(model, payload.get("query") or "", session_id)
    except Exception:
        log.exception("durable title spawn check failed (non-fatal)")

async def _on_final_listing(*, session_id: str, run_id: str, payload: dict[str, Any], final_event: dict | None, failed: bool) -> None:
    if final_event is None:
        return
    try:
        save_listing_run(
            session_id=session_id,
            product_id=str(final_event.get("product_id") or ""),
            market_code=str(final_event.get("market_code") or "US"),
            listing={
                "listing": final_event.get("listing"),
                "issues": final_event.get("issues") or [],
                "hard_failed": bool(final_event.get("hard_failed")),
                "candidate": payload.get("candidate") or {},
            },
            run_id=run_id,
        )
    except Exception:
        log.exception("durable listing save failed (non-fatal)")

async def _on_final_chat(*, session_id: str, run_id: str, payload: dict[str, Any], final_event: dict | None, failed: bool) -> None:
    try:
        current_meta = get_session_meta(session_id=session_id)
        if not current_meta.get("title"):
            model = agents_models.get("selection")
            if model is not None:
                _spawn_title_task(model, payload.get("query") or "", session_id)
    except Exception:
        log.exception("durable chat title spawn failed (non-fatal)")

async def _flush_observability(_app: web.Application) -> None:
    """langfuse-observability — flush pending traces on graceful shutdown."""
    observability.flush()

def build_app() -> web.Application:
    app = web.Application()
    app.middlewares.append(local_source_guard)
    # langfuse-observability — flush pending traces on graceful shutdown
    app.on_cleanup.append(_flush_observability)
    # durable-agent-tasks — DBOS launch + approval/durable switch matrix
    if durable.approval_enabled() and not durable.enabled():
        raise StartupConfigError(
            ["AGENT_APPROVAL=on requires AGENT_DURABLE=on (workflow-event semantics)"]
        )
    if durable.enabled():
        # 注册 per-kind 执行面(payload 全 JSON-safe;candidate 以 dict 传递,
        # driver_factory 内 ProductCandidate.model_validate 重建——崩溃重启后
        # 注册表随 build_app 重建,recovery 才能找到执行体)。
        durable.register_run_kind(
            "selection",
            driver_factory=lambda **payload: stream_run_loop(
                query=payload["query"],
                session_id=payload["session_id"],
                run_id=payload["run_id"],
                history_context=payload.get("history_context") or "",
                agents_models=agents_models,
            ),
            on_final=_on_final_selection,
        )
        durable.register_run_kind(
            "listing",
            driver_factory=lambda **payload: stream_run_listing_loop(
                candidate=ProductCandidate.model_validate(payload["candidate"]),
                market_code=payload["market_code"],
                brand=payload["brand"],
                competitor_brands=payload["competitor_brands"],
                session_id=payload["session_id"],
                run_id=payload["run_id"],
                agents_models=agents_models,
            ),
            on_final=_on_final_listing,
            approval=durable.approval_enabled(),
        )
        durable.register_run_kind(
            "chat",
            driver_factory=lambda **payload: stream_general_loop(
                query=payload["query"],
                session_id=payload["session_id"],
                run_id=payload["run_id"],
                history_context=payload.get("history_context") or "",
                agents_models=agents_models,
            ),
            on_final=_on_final_chat,
        )
        app.on_startup.append(lambda _app: durable.ensure_launched())
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/config", config_snapshot)
    app.router.add_get("/history", history)
    app.router.add_get("/history/{run_id}", history_detail)
    app.router.add_get("/sessions", list_sessions_handler)
    app.router.add_delete("/sessions", delete_all_sessions_handler)
    app.router.add_get(
        "/sessions/{session_id}/events", session_events_handler
    )
    app.router.add_delete(
        "/sessions/{session_id}", delete_session_handler
    )
    app.router.add_patch(
        "/sessions/{session_id}", session_meta_update_handler
    )
    app.router.add_post("/compact", compact_session_handler)
    app.router.add_post("/run", run)
    app.router.add_post("/chat", chat_run)
    app.router.add_post("/listing/run", listing_run)
    app.router.add_post("/listing/export", listing_export)
    app.router.add_post("/listing/upload", listing_upload)
    app.router.add_post("/listing/regen-field", listing_regen_field)
    app.router.add_get("/listing/channel", listing_channel)
    app.router.add_get("/listing/runs", listing_runs_list)
    app.router.add_get("/listing/runs/{run_id}", listing_run_detail)
    app.router.add_patch("/listing/runs/{run_id}", listing_run_patch)
    app.router.add_delete("/listing/runs/{run_id}", listing_run_delete)
    app.router.add_patch("/listing/runs/{run_id}/fields", listing_patch_fields_handler)
    app.router.add_post("/listing/gen-image", listing_gen_image_handler)
    app.router.add_post("/runs/{run_id}/approval", approval_resolve_handler)
    app.router.add_get("/runs/approvals", approvals_list_handler)
    app.router.add_get("/preferences", preferences_list)
    app.router.add_post("/preferences", preferences_add)
    app.router.add_delete("/preferences/{id}", preferences_delete)
    app.router.add_patch("/preferences/{id}", preferences_patch)
    app.router.add_get("/config/spapi", spapi_config_get)
    app.router.add_post("/config/spapi", spapi_config_save)
    app.router.add_post("/config/providers", config_providers)
    app.router.add_post("/config/models", config_models)
    app.router.add_post("/config/test", config_test)
    app.router.add_post("/config/save", config_save)
    app.router.add_get("/config/saved", saved_providers)
    app.router.add_post("/config/saved", save_provider)
    app.router.add_post("/config/saved/delete", delete_provider)
    app.router.add_get("/config/model", model_config_get)
    app.router.add_post("/config/model", model_config_save)
    return app


def _start_stdin_watchdog() -> threading.Thread:
    """stdin-EOF watchdog. The bridge keeps our stdin as a pipe so when
    the parent (Node dev server) is killed /F, OOM-killed, or otherwise
    closes the pipe, we read EOF here and shut the server down. Cross-
    platform and zero-dependency — the same pattern codex uses for MCP
    stdio servers (where the parent owns the pipe and a peer disconnect
    is the canonical shutdown signal). Windows has no signal simulation
    for parent death, so this is the *only* reliable orphan-kill
    mechanism short of Job Object (which we explicitly do not depend on —
    pnpm#12406: JS ecosystem has no mainstream zero-dependency solution).
    """
    def _watch() -> None:
        try:
            sys.stdin.buffer.read()
        except Exception:  # noqa: BLE001
            pass
        # EOF received → clean shutdown. log.warning so AGENT_BRIDGE_DEBUG
        # users see the cause; default users get the same exit code as a
        # graceful Ctrl+C.
        logging.warning("stdin EOF; shutting down agent server")
        sys.exit(0)
    t = threading.Thread(target=_watch, name="stdin-watchdog", daemon=True)
    t.start()
    return t


def _write_pid_file(data_dir: str) -> None:
    """drop a pid file under data_dir so the operator can locate and
    kill stale processes from a previous dev session. The agent-port-unique
    filename avoids two dev servers (different AGENT_PORT) clobbering each
    other's pid file.
    """
    try:
        os.makedirs(data_dir, exist_ok=True)
        port = int(os.environ.get("AGENT_PORT", "8765"))
        path = os.path.join(data_dir, f"agent-server-{port}.pid")
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception as e:  # noqa: BLE001
        logging.warning("could not write pid file: %s", e)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    # G0.2 — startup identity line on stderr. stdout is not consumed by the
    # bridge (it goes to ignore — see lib/agent-bridge.ts); stderr is the only
    # channel reliably surfaced to the operator. Bridge collects a ring buffer
    # of stderr  and dumps the tail on non-zero exit, so this line shows
    # up without AGENT_BRIDGE_DEBUG.
    print(
        f"[agent-server] pid={os.getpid()} port={int(os.environ.get('AGENT_PORT', '8765'))}"
        f" data_dir={os.environ.get('AGENT_DATA_DIR') or '(default)'}",
        file=sys.stderr,
        flush=True,
    )
    # pid file for orphan cleanup (best-effort, non-fatal).
    data_dir = os.environ.get("AGENT_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "data",
        "selection",
    )
    _write_pid_file(data_dir)
    _start_stdin_watchdog()
    port = int(os.environ.get("AGENT_PORT", "8765"))
    web.run_app(build_app(), host="127.0.0.1", port=port, access_log=None)


if __name__ == "__main__":
    main()