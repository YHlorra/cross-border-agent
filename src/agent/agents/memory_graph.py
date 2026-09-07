"""记忆图：entity_edges 表 + 确定性物化 + graph_traverse。

零 LLM 索引：选品 run 结束时由确定性代码写类型化边——本 run 的
report 与上一 run 同 session 同关键词 report 之间建 supersedes（旧边
valid_until 闭合，双时间线）；top 候选与竞品快照建 based_on / matched_to；
候选 in_category 品类节点；Listing 产物流 produced_listing。全部实体用
稳定 id（product_id / run_id / asin / 品类 slug），构造即消解。

graph_traverse 是记忆图唯一读侧工具（hops≤2 有界遍历，纯 SQL join，
返回类型化路径文本）。

SQL 移植 PG 方言（%s 占位 / RETURNING id / dict_row），
schema 由 db/（Drizzle）独占——ensure 退化为 init_db 存在性校验。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..persistence.pg_store import _conn, init_db

# 动词表固定 7 个（小词表纪律）：未知 rel 拒写。
EDGE_RELS = frozenset(
    {
        "supersedes",
        "based_on",
        "matched_to",
        "in_category",
        "decided",
        "produced_listing",
        "competitor_of",
    }
)

_ENTITY_TYPES = frozenset({"product", "run", "amazon", "category", "listing"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_entity_edges_table(db_path: Path | str | None = None) -> None:
    """PG-only：表结构归 Drizzle，这里只做存在性校验（幂等）。"""
    init_db(db_path)


def write_edge(
    *,
    src_type: str,
    src_id: str,
    rel: str,
    dst_type: str,
    dst_id: str,
    run_id: str = "",
    db_path: Path | str | None = None,
) -> int:
    """Insert one typed edge. Unknown rel → ValueError (小词表纪律)."""
    if rel not in EDGE_RELS:
        raise ValueError(f"unknown edge rel: {rel!r} (allowed: {sorted(EDGE_RELS)})")
    if src_type not in _ENTITY_TYPES or dst_type not in _ENTITY_TYPES:
        raise ValueError(f"unknown entity type (allowed: {sorted(_ENTITY_TYPES)})")
    ensure_entity_edges_table(db_path)
    now = _now()
    with _conn(db_path) as conn:
        row = conn.execute(
            """INSERT INTO entity_edges
               (src_type, src_id, rel, dst_type, dst_id, valid_from, valid_until, run_id, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s)
               RETURNING id""",
            (src_type, src_id, rel, dst_type, dst_id, now, run_id, now),
        ).fetchone()
        conn.commit()
        return int(row["id"])


def close_open_edges(
    *,
    src_type: str,
    src_id: str,
    rel: str,
    until: Optional[str] = None,
    db_path: Path | str | None = None,
) -> int:
    """Close open (valid_until IS NULL) edges of one (src, rel) — supersede
    双时间线：新边落地前把旧边闭合。"""
    ensure_entity_edges_table(db_path)
    with _conn(db_path) as conn:
        cur = conn.execute(
            """UPDATE entity_edges SET valid_until = %s
               WHERE src_type = %s AND src_id = %s AND rel = %s AND valid_until IS NULL""",
            (until or _now(), src_type, src_id, rel),
        )
        conn.commit()
        return cur.rowcount


def traverse_edges(
    *,
    entity_type: str,
    entity_id: str,
    rel: Optional[str] = None,
    hops: int = 1,
    db_path: Path | str | None = None,
) -> list[dict]:
    """有界遍历（hops≤2）：从 (entity_type, entity_id) 出发沿类型化边走。

    返回 [{src_type, src_id, rel, dst_type, dst_id, valid_from, valid_until,
    run_id}]——纯 SQL join，零 LLM。未指定 rel 时返回全部关系。
    """
    if hops not in (1, 2):
        raise ValueError("hops must be 1 or 2 (有界遍历)")
    ensure_entity_edges_table(db_path)
    with _conn(db_path) as conn:
        if hops == 1:
            where = ["(src_type = %s AND src_id = %s)"]
            params: list = [entity_type, entity_id]
            if rel:
                where.append("rel = %s")
                params.append(rel)
            rows = conn.execute(
                f"""SELECT src_type, src_id, rel, dst_type, dst_id,
                           valid_from, valid_until, run_id
                    FROM entity_edges
                    WHERE {' AND '.join(where)} AND valid_until IS NULL
                    ORDER BY id""",
                params,
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT e1.src_type AS s1t, e1.src_id AS s1i, e1.rel AS r1,
                          e1.dst_type AS s2t, e1.dst_id AS s2i, e1.valid_until AS v1,
                          e2.rel AS r2, e2.dst_type AS s3t, e2.dst_id AS s3i,
                          e2.valid_until AS v2
                   FROM entity_edges e1
                   JOIN entity_edges e2
                     ON e2.src_type = e1.dst_type AND e2.src_id = e1.dst_id
                    AND e1.valid_until IS NULL AND e2.valid_until IS NULL
                   WHERE e1.src_type = %s AND e1.src_id = %s
                     AND (%s::text IS NULL OR e2.rel = %s)
                   ORDER BY e1.id, e2.id""",
                (entity_type, entity_id, rel, rel),
            ).fetchall()
    if hops == 1:
        return [dict(r) for r in rows]
    return [
        {
            # 两跳路径的起点（与 hops=1 同构，方便渲染）：起点即查询实体
            "src_type": entity_type,
            "src_id": entity_id,
            "rel": r["r1"],
            "dst_type": r["s2t"],
            "dst_id": r["s2i"],
            "valid_from": None,
            "valid_until": r["v1"],
            "run_id": None,
            "via": {"rel": r["r2"], "dst_type": r["s3t"], "dst_id": r["s3i"]},
        }
        for r in rows
    ]


def render_traverse_paths(rows: list[dict]) -> str:
    """把遍历结果渲染成类型化路径文本（≤~1k token，工具描述按问题类型路由）。"""
    if not rows:
        return "(无相关记忆)"
    lines: list[str] = []
    for r in rows:
        via = r.get("via")
        if via:
            lines.append(
                f"{r['src_type']}:{r['src_id']} --{r['rel']}--> "
                f"{r['dst_type']}:{r['dst_id']} --{via['rel']}--> "
                f"{via['dst_type']}:{via['dst_id']}"
            )
        else:
            until = r.get("valid_until")
            span = "" if not until else f" (valid_until={until[:10]})"
            lines.append(
                f"{r['src_type']}:{r['src_id']} --{r['rel']}--> "
                f"{r['dst_type']}:{r['dst_id']}{span}"
            )
    return "\n".join(lines)[:2000]


def _prior_selection_run(
    session_id: str, keyword: str, exclude_run_id: str = "", db_path: Path | str | None = None
) -> Optional[dict]:
    """同一 session 同一 seed_keyword 的上一 run envelope（supersedes 源）。

    只比较同 session 的既往 run（通过 messages 表归属）；keyword 匹配
    seed_keyword；排除当前 run 自身。"""
    with _conn(db_path) as conn:
        row = conn.execute(
            """SELECT sr.id, sr.query, sr.seed_keyword, sr.decision,
                      sr.report_json, sr.created_at
               FROM selection_runs sr
               JOIN (
                    SELECT DISTINCT run_id FROM messages WHERE session_id = %s
               ) m ON m.run_id = sr.id
               WHERE sr.seed_keyword = %s AND sr.id != %s
               ORDER BY sr.created_at DESC LIMIT 1""",
            (session_id, keyword, exclude_run_id),
        ).fetchone()
    if row is None:
        return None
    try:
        report = json.loads(row["report_json"]) if row["report_json"] else {}
    except (json.JSONDecodeError, TypeError):
        report = {}
    return {
        "id": row["id"],
        "query": row["query"],
        "seed_keyword": row["seed_keyword"] or "",
        "decision": row["decision"] or "",
        "report": report if isinstance(report, dict) else {},
        "created_at": row["created_at"],
    }


def materialize_edges_for_run(
    *,
    run_id: str,
    session_id: str,
    keyword: str,
    decision: str,
    report: Optional[dict],
    candidates: list[dict],
    db_path: Path | str | None = None,
) -> int:
    """persist 物化（4.2）：loop run 的 final 落库路径上调用的确定性写边。

    零 LLM 调用。返回写入的边数。
    边集：
      run --decided--> product           （本 run report 的 top 候选）
      run --supersedes--> prior_run      （同 session 同关键词上一 run；旧 run 边不动，
                                          本 run 与 prior 之间新建时序边即可——supersede
                                          语义由 traverse 的 valid_until 表达）
      product --based_on--> amazon       （候选 `_competitor` 快照 asin）
      product --matched_to--> amazon     （同 asin 显式匹配）
      product --in_category--> category  （品类 slug）
      product --competitor_of--> amazon  （同品类竞品）
      listing run --produced_listing--> run  ——本函数只写选品侧
    """
    ensure_entity_edges_table(db_path)
    written = 0
    top = max(candidates, key=lambda c: c.get("total_score") or 0) if candidates else None
    if top is None:
        return 0
    top_id = str(top.get("product_id") or "")
    if top_id:
        written += write_edge(
            src_type="run", src_id=run_id, rel="decided", dst_type="product", dst_id=top_id,
            run_id=run_id, db_path=db_path,
        )

    # an empty seed_keyword would match every run in the session on
    # the "" equality below, chaining unrelated runs into a supersedes chain.
    # No keyword → no supersede claim (decided/based_on/in_category below are
    # keyword-independent and stay).
    if keyword.strip():
        prior = _prior_selection_run(session_id, keyword, run_id, db_path)
        if prior:
            written += write_edge(
                src_type="run", src_id=run_id, rel="supersedes", dst_type="run", dst_id=prior["id"],
                run_id=run_id, db_path=db_path,
            )
            # 旧 run 与它自己的 top product 之间的 decided 边闭合（该 run 已被新 run 取代）
            prior_top = _top_of_envelope(prior.get("report") or {})
            if prior_top:
                close_open_edges(src_type="run", src_id=prior["id"], rel="decided", db_path=db_path)
                written += write_edge(
                    src_type="run", src_id=prior["id"], rel="decided", dst_type="product",
                    dst_id=str(prior_top), run_id=prior["id"], db_path=db_path,
                )

    for c in candidates:
        pid = str(c.get("product_id") or "")
        if not pid:
            continue
        cat = c.get("category")
        if cat:
            written += write_edge(
                src_type="product", src_id=pid, rel="in_category",
                dst_type="category", dst_id=str(cat), run_id=run_id, db_path=db_path,
            )
        comp = c.get("_competitor") if isinstance(c.get("_competitor"), dict) else None
        asin = comp.get("asin") if comp else None
        if asin:
            written += write_edge(
                src_type="product", src_id=pid, rel="based_on",
                dst_type="amazon", dst_id=str(asin), run_id=run_id, db_path=db_path,
            )
    return written


def _top_of_envelope(envelope: dict) -> Optional[str]:
    cands = envelope.get("candidates") if isinstance(envelope, dict) else None
    if not isinstance(cands, list) or not cands:
        return None
    top = max((c for c in cands if isinstance(c, dict)), key=lambda c: c.get("total_score") or 0)
    return top.get("product_id") if isinstance(top, dict) else None
