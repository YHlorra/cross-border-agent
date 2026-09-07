"""评分公式。

    score = (1 / (1 + distance)) * (1 + log(1 + rc)) * exp(-age / half_life)

distance: sqlite-vec 返回的 L2 距离（越小越相似）
rc: reinforcement_count（用户重复表达同一偏好 → 权重↑）
age: 与 last_reinforced_at 的天数差（→ 软过期）
half_life_days: 30（默认）
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

HALF_LIFE_DAYS: float = 30.0


def _parse_iso(ts: str) -> datetime:
    """宽容的 ISO-8601 解析（容忍 Z 后缀 + 缺秒）。"""
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.now(timezone.utc)


def age_days_from(last_reinforced_at: str, now: datetime | None = None) -> float:
    """last_reinforced_at 距 now 的天数。"""
    if now is None:
        now = datetime.now(timezone.utc)
    t = _parse_iso(last_reinforced_at)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, (now - t).total_seconds() / 86_400.0)


def score(
    distance: float,
    reinforcement_count: int,
    age_days: float,
    half_life_days: float = HALF_LIFE_DAYS,
) -> float:
    """综合评分（越大越相关）。"""
    similarity = 1.0 / (1.0 + max(0.0, distance))
    reinforce = 1.0 + math.log1p(max(0, reinforcement_count))  # log1p(0)=0 → 1
    decay = math.exp(-age_days / max(1.0, half_life_days))
    return similarity * reinforce * decay
