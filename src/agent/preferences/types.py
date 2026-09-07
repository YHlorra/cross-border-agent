"""偏好类型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# 4 类偏好（ §二）
VALID_CATEGORIES: frozenset[str] = frozenset(
    {"budget", "category", "logistics", "platform"}
)


@dataclass(frozen=True)
class NewPreference:
    """LLM 抽取产出的「新偏好」形态（before insertion）。"""

    category: str
    preference_text: str
    confidence: float = 1.0  # 0-1；< 0.6 不入库
    supersedes_id: int | None = None  # 与已有偏好矛盾时指向旧 id


@dataclass(frozen=True)
class PreferenceHit:
    """KNN + decay 评分后的检索结果（注入到 intent LLM 上下文）。"""

    id: int
    category: str
    preference_text: str
    distance: float
    reinforcement_count: int
    age_days: float
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
