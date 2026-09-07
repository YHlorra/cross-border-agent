"""KNN 检索 + decay 评分。

Pipeline: embed(query) → pgvector L2 KNN (top-N candidates) → join metadata →
apply ``decay.score`` → sort → top-k。

SQLite/sqlite-vec 分支已删，PG 是唯一路径。
"""
from __future__ import annotations

from ..persistence import pg_store
from .types import PreferenceHit
from .decay import score, age_days_from


def retrieve(
    *,
    query_embedding: list[float],
    top_k: int = 5,
    knn_candidates: int = 20,
    category: str | None = None,
    half_life_days: float = 30.0,
    db_path: str | None = None,
) -> list[PreferenceHit]:
    """KNN + decay 检索，返回 top-k PreferenceHit（按 score 降序）。"""
    if not query_embedding:
        return []
    knn = pg_store.knn_preferences(query_embedding, knn_candidates)
    if not knn:
        return []
    meta_by_id = pg_store.metadata_for_ids([pid for pid, _ in knn])

    hits: list[PreferenceHit] = []
    for pid, dist in knn:
        meta = meta_by_id.get(pid)
        if not meta or not meta.get("active"):
            continue
        if meta.get("expires_at"):
            # simple string compare (ISO-8601) is monotonic
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).isoformat()
            if meta["expires_at"] < now:
                continue
        if category and meta["category"] != category:
            continue
        rc = int(meta.get("reinforcement_count") or 1)
        age_d = age_days_from(meta["last_reinforced_at"])
        s = score(dist, rc, age_d, half_life_days=half_life_days)
        hits.append(
            PreferenceHit(
                id=pid,
                category=meta["category"],
                preference_text=meta["preference_text"],
                distance=dist,
                reinforcement_count=rc,
                age_days=age_d,
                score=s,
                metadata={
                    "source_run_id": meta.get("source_run_id"),
                },
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]
