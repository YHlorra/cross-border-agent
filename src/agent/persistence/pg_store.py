"""Postgres backend for the persistence layer (postgres-data-platform).

Same public surface as ``store.py`` (signature-for-signature; ``db_path``
accepted and ignored — ``DATABASE_URL`` is the only switch). The SQLite
module keeps this contract by re-exporting ``*`` from here when
``DATABASE_URL`` is set (see store.py tail dispatcher).

Deviations from the SQLite internals (behaviour parity preserved):
- ``init_db`` performs a schema *presence check* only: Postgres schema is
  owned by ``db/`` (Drizzle). Missing tables → RuntimeError pointing at
  ``npx drizzle-kit migrate``.
- ``save_event`` in this module already writes with ``ON CONFLICT DO
  NOTHING`` semantics reserved for durable replay (durable-agent-tasks) —
  wait: parity first. This module mirrors SQLite's plain INSERT here;
  idempotency lands via durable-agent-tasks on both backends.
- ``ensure_preference_vec_table`` validates the pgvector extension instead
  of creating a sqlite-vec virtual table (vector lives in the
  ``preference_embeddings.embedding`` column).
- ``knn_preferences`` is PG-only: pgvector L2 KNN used by
  preferences/retrieve.py when DATABASE_URL is set.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

__all__ = [
    "init_db",
    "save_run",
    "get_run",
    "list_runs",
    "save_listing_run",
    "update_listing_field",
    "get_listing_run",
    "list_listing_runs",
    "rename_listing_run",
    "delete_listing_run",
    "save_message",
    "list_messages",
    "save_event",
    "list_session_events",
    "list_session_runs",
    "list_sessions",
    "get_session_meta",
    "update_session_meta",
    "delete_session_events",
    "mark_run_failed",
    "ensure_preference_vec_table",
    "insert_preference",
    "reinforce_preference",
    "supersede_preference",
    "list_active_preferences",
    "delete_preference",
    "update_preference",
    "knn_preferences",
    "metadata_for_ids",
    "record_approval",
    "resolve_approval",
    "list_approvals",
]

_REQUIRED_TABLES = (
    "selection_runs",
    "listing_runs",
    "messages",
    "entity_edges",
    "preference_embeddings",
    "session_meta",
)


def _url(db_path: Path | str | None = None) -> str:
    """Resolve the connection string (``db_path`` is accepted for parity)."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is required (PG-only persistence). "
            "Set it in .env.local (e.g. postgresql://postgres@127.0.0.1:5433/crossborder) "
            "and start the instance with scripts/pg_up.bat"
        )
    return url


def _conn(db_path: Path | str | None = None) -> Any:
    return psycopg.connect(_url(db_path), row_factory=dict_row)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- envelope/listing 容错摘要（原 store.py 纯函数，自含）----


def _summary_from_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _top_summary(report_json: str | None) -> dict | None:
    """Top candidate (name + score) from a stored run envelope; legacy → None."""
    data = _summary_from_json(report_json)
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not candidates:
        return None
    top = max(candidates, key=lambda c: c.get("total_score") or 0)
    return {"top_name": top.get("name_cn"), "top_score": top.get("total_score")}


def _intent_summary(report_json: str | None) -> dict | None:
    """Persisted intent (budget + preferences) for history replay; legacy → None."""
    data = _summary_from_json(report_json)
    intent = data.get("intent") if isinstance(data, dict) else None
    if not isinstance(intent, dict):
        return None
    return {
        "budget_cny": intent.get("budget_cny"),
        "preferences": intent.get("preferences") or [],
    }


def _listing_summary(listing_json: str | None) -> dict:
    """Fault-tolerant listing summary; one bad row can't 500 the whole list."""
    out = {"product_name": "", "listing_title": "", "hard_failed": False}
    data = _summary_from_json(listing_json)
    if not isinstance(data, dict):
        return out
    out["hard_failed"] = bool(data.get("hard_failed"))
    listing = data.get("listing")
    if isinstance(listing, dict):
        out["listing_title"] = listing.get("item_name") or ""
    candidate = data.get("candidate")
    if isinstance(candidate, dict):
        out["product_name"] = candidate.get("name_en") or candidate.get("name_cn") or ""
    if not out["product_name"] and isinstance(listing, dict):
        out["product_name"] = listing.get("item_name") or ""
    return out


# ---- init -------------------------------------------------------------------


def init_db(db_path: Path | str | None = None) -> None:
    """Schema presence check — Postgres DDL is owned by ``db/`` (Drizzle)."""
    with _conn(db_path) as conn:
        cur = conn.execute(
            """SELECT table_name FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = ANY(%s)""",
            (list(_REQUIRED_TABLES),),
        )
        found = {r["table_name"] for r in cur.fetchall()}
    missing = [t for t in _REQUIRED_TABLES if t not in found]
    if missing:
        raise RuntimeError(
            "Postgres schema incomplete (missing: "
            + ", ".join(missing)
            + "); run `npm run migrate` in db/ (drizzle-kit) first"
        )


# ---- selection runs ---------------------------------------------------------


def save_run(
    *,
    query: str,
    seed_keyword: str,
    decision: str,
    report: Any,
    run_id: str | None = None,
    db_path: Path | str | None = None,
) -> str:
    init_db(db_path)
    rid = run_id or str(uuid.uuid4())
    with _conn(db_path) as conn:
        conn.execute(
            """INSERT INTO selection_runs
               (id, query, seed_keyword, decision, report_json, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET
                 query = EXCLUDED.query,
                 seed_keyword = EXCLUDED.seed_keyword,
                 decision = EXCLUDED.decision,
                 report_json = EXCLUDED.report_json,
                 created_at = EXCLUDED.created_at""",
            (
                rid,
                query,
                seed_keyword,
                decision,
                json.dumps(report, ensure_ascii=False, default=str),
                _now(),
            ),
        )
        conn.commit()
    return rid


def get_run(run_id: str, db_path: Path | str | None = None) -> dict | None:
    init_db(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            """SELECT id, query, seed_keyword, decision, report_json, created_at
               FROM selection_runs WHERE id = %s""",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    envelope: dict = {}
    if row["report_json"]:
        try:
            parsed = json.loads(row["report_json"])
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            envelope = parsed
    is_envelope = "report" in envelope
    return {
        "id": row["id"],
        "query": row["query"],
        "seed_keyword": row["seed_keyword"] or "",
        "decision": row["decision"] or "",
        "report": envelope.get("report") if is_envelope else envelope or None,
        "candidates": envelope.get("candidates") or [],
        "intent": envelope.get("intent") if is_envelope else None,
        "created_at": row["created_at"],
    }


def list_runs(limit: int = 20, db_path: Path | str | None = None) -> list[dict]:
    init_db(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT id, query, seed_keyword, decision, report_json, created_at
               FROM selection_runs
               ORDER BY created_at DESC
               LIMIT %s""",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "query": r["query"],
            "seed_keyword": r["seed_keyword"],
            "decision": r["decision"],
            "top": _top_summary(r["report_json"]),
            "intent": _intent_summary(r["report_json"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


# ---- listing runs -----------------------------------------------------------


def save_listing_run(
    *,
    session_id: str,
    product_id: str,
    market_code: str,
    listing: Any,
    run_id: str | None = None,
    db_path: Path | str | None = None,
) -> str:
    init_db(db_path)
    rid = run_id or str(uuid.uuid4())
    with _conn(db_path) as conn:
        conn.execute(
            """INSERT INTO listing_runs
               (id, session_id, product_id, market_code, listing_json, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET
                 session_id = EXCLUDED.session_id,
                 product_id = EXCLUDED.product_id,
                 market_code = EXCLUDED.market_code,
                 listing_json = EXCLUDED.listing_json,
                 created_at = EXCLUDED.created_at""",
            (
                rid,
                session_id,
                product_id,
                market_code,
                json.dumps(listing, ensure_ascii=False, default=str),
                _now(),
            ),
        )
        conn.commit()
    return rid


def update_listing_field(
    run_id: str,
    field_name: str,
    new_value: Any,
    db_path: Path | str | None = None,
) -> None:
    init_db(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT listing_json FROM listing_runs WHERE id = %s", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"listing run not found: {run_id}")
        listing = json.loads(row["listing_json"])
        listing[field_name] = new_value
        conn.execute(
            "UPDATE listing_runs SET listing_json = %s WHERE id = %s",
            (
                json.dumps(listing, ensure_ascii=False, default=str),
                run_id,
            ),
        )
        conn.commit()


def get_listing_run(run_id: str, db_path: Path | str | None = None) -> dict | None:
    init_db(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            """SELECT id, session_id, product_id, market_code, listing_json, title, created_at
               FROM listing_runs WHERE id = %s""",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        listing = json.loads(row["listing_json"])
    except (json.JSONDecodeError, TypeError):
        listing = None
    summary = _listing_summary(row["listing_json"])
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "product_id": row["product_id"],
        "product_name": summary["product_name"],
        "market_code": row["market_code"],
        "listing": listing,
        "listing_title": summary["listing_title"],
        "title": row["title"],
        "created_at": row["created_at"],
    }


def list_listing_runs(limit: int = 50, db_path: Path | str | None = None) -> list[dict]:
    init_db(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT id, session_id, product_id, market_code, listing_json, title, created_at
               FROM listing_runs
               ORDER BY created_at DESC
               LIMIT %s""",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        summary = _listing_summary(r["listing_json"])
        out.append(
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "product_id": r["product_id"],
                "market_code": r["market_code"],
                "product_name": summary["product_name"],
                "listing_title": summary["listing_title"],
                "title": r["title"],
                "hard_failed": summary["hard_failed"],
                "created_at": r["created_at"],
            }
        )
    return out


def rename_listing_run(
    *, run_id: str, title: str, db_path: Path | str | None = None
) -> dict | None:
    clean = (title or "").strip() or None
    init_db(db_path)
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE listing_runs SET title = %s WHERE id = %s",
            (clean, run_id),
        )
        if cur.rowcount == 0:
            exists = conn.execute(
                "SELECT 1 FROM listing_runs WHERE id = %s", (run_id,)
            ).fetchone()
            if not exists:
                return None
        conn.commit()
    return get_listing_run(run_id, db_path=db_path)


def delete_listing_run(*, run_id: str, db_path: Path | str | None = None) -> bool:
    init_db(db_path)
    with _conn(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM listing_runs WHERE id = %s", (run_id,)
        )
        removed = cur.rowcount > 0
        conn.commit()
    return removed


# ---- messages ---------------------------------------------------------------


def save_message(
    session_id: str,
    role: str,
    content: str,
    db_path: Path | str | None = None,
) -> str:
    return save_event(
        session_id=session_id,
        run_id=f"legacy-{role}",
        event_type="message",
        sequence=0,
        payload={"role": role, "content": content},
        db_path=db_path,
    )


def list_messages(
    session_id: str, limit: int = 100, db_path: Path | str | None = None
) -> list[dict]:
    rows = list_session_events(
        session_id=session_id,
        event_type="message",
        limit=limit,
        db_path=db_path,
    )
    return [
        {
            "id": r["id"],
            "role": (r["payload"] or {}).get("role", ""),
            "content": (r["payload"] or {}).get("content", ""),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def save_event(
    *,
    session_id: str,
    run_id: str,
    event_type: str,
    sequence: int,
    payload: Any,
    answer_failed: bool = False,
    db_path: Path | str | None = None,
) -> str:
    """Mirror of store.save_event (plain INSERT — see module docstring:
    idempotent variant lands with durable-agent-tasks on both backends)."""
    init_db(db_path)
    mid = str(uuid.uuid4())
    with _conn(db_path) as conn:
        conn.execute(
            """INSERT INTO messages
               (id, session_id, run_id, event_type, sequence, payload, answer_failed, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT(session_id, run_id, sequence) DO NOTHING""",
            (
                mid,
                session_id,
                run_id,
                event_type,
                sequence,
                json.dumps(payload, ensure_ascii=False, default=str),
                1 if answer_failed else 0,
                _now(),
            ),
        )
        conn.commit()
    return mid


def list_session_events(
    *,
    session_id: str,
    event_type: str | list[str] | None = None,
    include_failed: bool = True,
    limit: int = 1000,
    db_path: Path | str | None = None,
) -> list[dict]:
    init_db(db_path)
    with _conn(db_path) as conn:
        where = ["session_id = %s"]
        params: list[Any] = [session_id]
        if not include_failed:
            where.append("answer_failed = 0")
        if event_type is not None:
            types = [event_type] if isinstance(event_type, str) else list(event_type)
            if types:
                placeholders = ", ".join("%s" for _ in types)
                where.append(f"event_type IN ({placeholders})")
                params.extend(types)
        cur = conn.execute(
            f"""SELECT id, session_id, run_id, event_type, sequence, payload, answer_failed, created_at
                FROM messages AS m
                WHERE {' AND '.join(f'm.{clause}' for clause in where)}
                ORDER BY (
                    SELECT MIN(first.created_at)
                    FROM messages AS first
                    WHERE first.session_id = m.session_id
                      AND first.run_id = m.run_id
                ) ASC,
                m.sequence ASC,
                m.created_at ASC,
                m.id ASC
                LIMIT %s""",
            (*params, limit),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r["id"],
            "session_id": r["session_id"],
            "run_id": r["run_id"],
            "event_type": r["event_type"],
            "sequence": r["sequence"],
            "payload": json.loads(r["payload"]) if r["payload"] else None,
            "answer_failed": bool(r["answer_failed"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def list_session_runs(
    *, session_id: str, limit: int = 100, db_path: Path | str | None = None
) -> list[dict]:
    init_db(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT run_id,
                      MIN(created_at) AS first_event_at,
                      MAX(created_at) AS last_event_at,
                      MAX(answer_failed) AS answer_failed,
                      COUNT(*) AS event_count,
                      MAX(CASE WHEN event_type='final' THEN payload ELSE NULL END) AS final_payload
               FROM messages
               WHERE session_id = %s
               GROUP BY run_id
               ORDER BY MIN(created_at) DESC
               LIMIT %s""",
            (session_id, limit),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        final: Any = (
            json.loads(r["final_payload"]) if r["final_payload"] else None
        )
        out.append(
            {
                "run_id": r["run_id"],
                "first_event_at": r["first_event_at"],
                "last_event_at": r["last_event_at"],
                "answer_failed": bool(r["answer_failed"]),
                "event_count": r["event_count"],
                "decision": final.get("decision") if isinstance(final, dict) else "",
            }
        )
    return out


# ---- sessions ---------------------------------------------------------------


def list_sessions(
    *,

    db_path: Path | str | None = None,
) -> list[dict]:
    init_db(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT m.session_id,
                      MIN(m.created_at) AS first_event_at,
                      MAX(m.created_at) AS last_event_at,
                      COUNT(*) AS event_count,
                      COUNT(DISTINCT m.run_id) AS run_count,
                      MAX(m.answer_failed) AS has_failed,
                      sm.title,
                      COALESCE(sm.pinned, 0) AS pinned,
                      sm.last_read_at
               FROM messages m
               LEFT JOIN session_meta sm ON sm.session_id = m.session_id
               GROUP BY m.session_id, sm.title, sm.pinned, sm.last_read_at
               ORDER BY COALESCE(sm.pinned, 0) DESC, MAX(m.created_at) DESC"""
        ).fetchall()
    out = [
        {
            "session_id": r["session_id"],
            "first_event_at": r["first_event_at"],
            "last_event_at": r["last_event_at"],
            "event_count": r["event_count"],
            "run_count": r["run_count"],
            "has_failed": bool(r["has_failed"]),
            "title": r["title"],
            "pinned": bool(r["pinned"]),
            "last_read_at": r["last_read_at"],
        }
        for r in rows
    ]
    return out


def get_session_meta(
    *, session_id: str, db_path: Path | str | None = None
) -> dict:
    init_db(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT title, pinned, last_read_at FROM session_meta WHERE session_id = %s",
            (session_id,),
        ).fetchone()
    if row is None:
        return {"title": None, "pinned": False, "last_read_at": None}
    return {
        "title": row["title"],
        "pinned": bool(row["pinned"]),
        "last_read_at": row["last_read_at"],
    }


def update_session_meta(
    *,
    session_id: str,
    fields: dict[str, Any],
    db_path: Path | str | None = None,
) -> dict:
    allowed = {"title", "pinned", "last_read_at"}
    clean = {k: v for k, v in fields.items() if k in allowed}
    if not clean:
        return get_session_meta(session_id=session_id, db_path=db_path)
    if "pinned" in clean:
        clean["pinned"] = 1 if clean["pinned"] else 0
    set_clause = ", ".join(f"{k} = EXCLUDED.{k}" for k in clean)
    init_db(db_path)
    now = _now()
    with _conn(db_path) as conn:
        cols = ["session_id", "updated_at", *clean.keys()]
        values = [session_id, now, *clean.values()]
        placeholders = ", ".join("%s" for _ in cols)
        conn.execute(
            f"""INSERT INTO session_meta ({", ".join(cols)})
               VALUES ({placeholders})
               ON CONFLICT(session_id) DO UPDATE SET
                   {set_clause},
                   updated_at = EXCLUDED.updated_at""",
            values,
        )
        conn.commit()
    return get_session_meta(session_id=session_id, db_path=db_path)


def delete_session_events(
    *, session_id: str, db_path: Path | str | None = None
) -> int:
    """删除任务 = 删聊天记录（messages 事件流 + 任务元数据）。

    选品报告（selection_runs）与 Listing 草稿（listing_runs）是产物，
    保留在各自列表——删除动机是整理聊天而非销毁产物
    （session-delete-semantics，2026-09-06）。"""
    init_db(db_path)
    with _conn(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM messages WHERE session_id = %s", (session_id,)
        )
        conn.execute(
            "DELETE FROM session_meta WHERE session_id = %s", (session_id,)
        )
        conn.commit()
        return cur.rowcount


def mark_run_failed(*, run_id: str, db_path: Path | str | None = None) -> int:
    init_db(db_path)
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE messages SET answer_failed = 1 WHERE run_id = %s",
            (run_id,),
        )
        conn.commit()
        return cur.rowcount


# ---- preferences (pgvector) --------------------------------------------------


def ensure_preference_vec_table(
    *, db_path: Path | str | None = None, dimensions: int = 1536
) -> None:
    """PG mode: the vector column lives on preference_embeddings (Drizzle).

    Validates the pgvector extension is installed; raises with remediation
    otherwise (idempotent, mirrors the sqlite-vec contract).
    """
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT extname FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
    if row is None:
        raise RuntimeError(
            "pgvector extension not installed; run `CREATE EXTENSION vector` "
            "on the target database, then `npm run migrate` in db/"
        )


def insert_preference(
    *,
    category: str,
    preference_text: str,
    embedding: list[float],
    source_run_id: str,
    source_session_id: str,
    expires_at: str | None = None,
    db_path: Path | str | None = None,
) -> int:
    init_db(db_path)
    ensure_preference_vec_table(db_path=db_path, dimensions=len(embedding))
    now = _now()
    vec = "[" + ",".join(repr(float(x)) for x in embedding) + "]"
    with _conn(db_path) as conn:
        row = conn.execute(
            """INSERT INTO preference_embeddings
               (category, preference_text, source_run_id, source_session_id,
                created_at, last_reinforced_at, reinforcement_count,
                expires_at, active, schema_version, embedding)
               VALUES (%s, %s, %s, %s, %s, %s, 1, %s, 1, 1, %s::vector)
               RETURNING id""",
            (
                category,
                preference_text,
                source_run_id,
                source_session_id,
                now,
                now,
                expires_at,
                vec,
            ),
        ).fetchone()
        conn.commit()
        return int(row["id"])


def reinforce_preference(*, preference_id: int, db_path: Path | str | None = None) -> int:
    init_db(db_path)
    with _conn(db_path) as conn:
        cur = conn.execute(
            """UPDATE preference_embeddings
               SET reinforcement_count = reinforcement_count + 1,
                   last_reinforced_at = %s
               WHERE id = %s AND active = 1""",
            (_now(), preference_id),
        )
        conn.commit()
        return cur.rowcount


def update_preference(
    *,
    preference_id: int,
    preference_text: str | None = None,
    category: str | None = None,
    embedding: list[float] | None = None,
    db_path: Path | str | None = None,
) -> int:
    """Patch editable fields of an active preference. When ``preference_text``
    changes the caller also passes a re-computed ``embedding`` (server layer
    is responsible for re-embedding). Returns rows updated (0 means not-found
    or already inactive)."""
    init_db(db_path)
    with _conn(db_path) as conn:
        sets: list[str] = []
        params: list[Any] = []
        if preference_text is not None:
            sets.append("preference_text = %s")
            params.append(preference_text)
        if category is not None:
            sets.append("category = %s")
            params.append(category)
        if embedding is not None:
            vec = "[" + ",".join(repr(float(x)) for x in embedding) + "]"
            sets.append("embedding = %s::vector")
            params.append(vec)
        if not sets:
            return 0
        params.append(preference_id)
        cur = conn.execute(
            f"UPDATE preference_embeddings SET {', '.join(sets)} "
            f"WHERE id = %s AND active = 1",
            params,
        )
        conn.commit()
        return cur.rowcount


def update_preference(
    *,
    preference_id: int,
    preference_text: str | None = None,
    category: str | None = None,
    embedding: list[float] | None = None,
    db_path: Path | str | None = None,
) -> int:
    """Patch editable fields of an active preference. When ``preference_text``
    changes the caller also passes a re-computed ``embedding`` (server layer
    is responsible for re-embedding). Returns rows updated (0 means
    not-found or already inactive)."""
    init_db(db_path)
    vec: str | None = None
    if embedding is not None:
        ensure_preference_vec_table(db_path=db_path, dimensions=len(embedding))
        vec = "[" + ",".join(repr(float(x)) for x in embedding) + "]"
    sets: list[str] = []
    params: list[object] = []
    if preference_text is not None:
        sets.append("preference_text = %s")
        params.append(preference_text)
    if category is not None:
        sets.append("category = %s")
        params.append(category)
    if vec is not None:
        sets.append("embedding = %s::vector")
        params.append(vec)
    if not sets:
        return 0
    params.append(preference_id)
    with _conn(db_path) as conn:
        cur = conn.execute(
            f"UPDATE preference_embeddings SET {', '.join(sets)} "
            "WHERE id = %s AND active = 1",
            params,
        )
        conn.commit()
        return cur.rowcount


def supersede_preference(
    *, old_id: int, new_id: int, db_path: Path | str | None = None
) -> None:
    init_db(db_path)
    with _conn(db_path) as conn:
        conn.execute(
            """UPDATE preference_embeddings
               SET active = 0, superseded_by = %s
               WHERE id = %s""",
            (new_id, old_id),
        )
        conn.commit()


def list_active_preferences(
    *,
    category: str | None = None,
    limit: int = 200,
    db_path: Path | str | None = None,
) -> list[dict]:
    init_db(db_path)
    with _conn(db_path) as conn:
        where = ["active = 1", "(expires_at IS NULL OR expires_at > %s)"]
        params: list[Any] = [_now()]
        if category:
            where.append("category = %s")
            params.append(category)
        rows = conn.execute(
            f"""SELECT id, category, preference_text, source_run_id,
                       source_session_id, created_at, last_reinforced_at,
                       reinforcement_count, expires_at
                FROM preference_embeddings
                WHERE {' AND '.join(where)}
                ORDER BY last_reinforced_at DESC
                LIMIT %s""",
            (*params, limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "category": r["category"],
            "preference_text": r["preference_text"],
            "source_run_id": r["source_run_id"],
            "source_session_id": r["source_session_id"],
            "created_at": r["created_at"],
            "last_reinforced_at": r["last_reinforced_at"],
            "reinforcement_count": r["reinforcement_count"],
            "expires_at": r["expires_at"],
        }
        for r in rows
    ]


def delete_preference(*, preference_id: int, db_path: Path | str | None = None) -> int:
    init_db(db_path)
    with _conn(db_path) as conn:
        cur = conn.execute(
            """UPDATE preference_embeddings
               SET active = 0, superseded_by = NULL
               WHERE id = %s AND active = 1""",
            (preference_id,),
        )
        conn.commit()
        return cur.rowcount


# ---- PG-only: pgvector KNN for preferences/retrieve.py ----------------------


def knn_preferences(
    query_embedding: list[float], k: int, db_path: Path | str | None = None
) -> list[tuple[int, float]]:
    """pgvector L2 KNN over active preferences — [(id, distance), ...].

    PG-only 后端：向量列在 preference_embeddings 表，
    由 Drizzle 迁移创建与 pgvector 扩展绑定。无 sqlite-vec 后备——DB 切换
    路径以 ``DATABASE_URL`` 是否存在为准。
    """
    init_db(db_path)
    vec = "[" + ",".join(repr(float(x)) for x in query_embedding) + "]"
    with _conn(db_path) as conn:
        rows = conn.execute(
            """SELECT id, embedding <-> %s::vector AS distance
               FROM preference_embeddings
               WHERE active = 1
               ORDER BY embedding <-> %s::vector
               LIMIT %s""",
            (vec, vec, k),
        ).fetchall()
    return [(int(r["id"]), float(r["distance"])) for r in rows]


def metadata_for_ids(ids: list[int], db_path: Path | str | None = None) -> dict[int, dict]:
    """Metadata map for retrieve.py's join step (PG mode)."""
    if not ids:
        return {}
    init_db(db_path)
    placeholders = ", ".join("%s" for _ in ids)
    with _conn(db_path) as conn:
        rows = conn.execute(
            f"""SELECT id, category, preference_text, last_reinforced_at,
                       reinforcement_count, active, expires_at, source_run_id
                FROM preference_embeddings WHERE id IN ({placeholders})""",
            list(ids),
        ).fetchall()
    return {int(r["id"]): dict(r) for r in rows}

# ---- durable-agent-tasks：审批记录 -------------------------------------------


def record_approval(
    *, run_id: str, session_id: str, kind: str, summary: str | None = None,
    db_path: Path | str | None = None,
) -> None:
    init_db(db_path)
    with _conn(db_path) as conn:
        conn.execute(
            """INSERT INTO approval_requests
               (run_id, session_id, kind, summary, status, created_at)
               VALUES (%s, %s, %s, %s, 'pending', %s)
               ON CONFLICT (run_id) DO NOTHING""",
            (run_id, session_id, kind, summary, _now()),
        )
        conn.commit()


def resolve_approval(
    *, run_id: str, decision: str, reason: str | None = None,
    db_path: Path | str | None = None,
) -> None:
    init_db(db_path)
    with _conn(db_path) as conn:
        conn.execute(
            """UPDATE approval_requests
               SET status = 'resolved', decision = %s, reason = %s, resolved_at = %s
               WHERE run_id = %s""",
            (decision, reason, _now(), run_id),
        )
        conn.commit()


def list_approvals(
    *, status: str = "pending", db_path: Path | str | None = None,
) -> list[dict]:
    init_db(db_path)
    with _conn(db_path) as conn:
        if status == "all":
            rows = conn.execute(
                "SELECT * FROM approval_requests ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM approval_requests WHERE status = %s ORDER BY created_at DESC",
                (status,),
            ).fetchall()
    return [dict(r) for r in rows]
