"""LLM-backed 偏好抽取。

LLM 从对话/选品结果中抽取 4 类偏好（budget/category/logistics/platform），
返回 ``list[NewPreference]``。

注意：原触发点（graph 的 persist_node）已随 graph 运行时退役，
当前无生产调用方——待偏好注入 loop 链路时复用或删除。

SEAM 友好：可注入 mock LLM（不依赖 aimux）测 prompt 解析 + 增量合并。
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from ..llm import AimuxChatModel
from .types import NewPreference, VALID_CATEGORIES

_EXTRACT_SYSTEM = """你是用户偏好抽取助手。从一段选品对话/结果中提取**稳定可复用的偏好信号**。
要求：
- 只提取跨场景适用的偏好（不提取单次具体的）
- 4 类：budget / category / logistics / platform
- 每条 confidence 0-1（< 0.6 不入库；调用方过滤）
- 若有 previous_preferences 列表：与之一致 → reinforce（返回相同 id 提示）；矛盾 → supersede
- 不要续写对话、不要调用工具；只输出 JSON
JSON 格式：[{"category": "budget", "preference_text": "想做 1000 以内", "confidence": 0.9, "supersedes_id": null}, ...]"""

_EXTRACT_INCREMENTAL = """你是用户偏好抽取助手。整合旧偏好与新对话片段。
- PRESERVE 旧偏好（除非新对话明确矛盾）
- ADD 新对话中新增的偏好
- 与旧偏好一致时不要重复输出；矛盾时用 supersedes_id 字段
- 只输出 JSON"""


def _format_conversation(messages: list[dict]) -> str:
    """轻量序列化：只保留 user/assistant 文本（防 LLM 偏题）。"""
    lines: list[str] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role") or m.get("event") or "?"
        text = m.get("text") or m.get("content") or m.get("delta") or ""
        if text and isinstance(text, str):
            lines.append(f"[{role}] {text[:500]}")
    return "\n".join(lines) or "(empty)"


async def extract_preferences(
    *,
    model: AimuxChatModel,
    conversation: list[dict],
    previous_preferences: list[dict] | None = None,
) -> list[NewPreference]:
    """LLM 抽取，返回 ``list[NewPreference]``。

    previous_preferences: 已有偏好列表（含 id/category/preference_text/confidence），
    用于增量合并。
    """
    sys_prompt = _EXTRACT_INCREMENTAL if previous_preferences else _EXTRACT_SYSTEM
    user_parts = []
    if previous_preferences:
        user_parts.append("已有偏好列表：")
        user_parts.append(
            json.dumps(
                [
                    {
                        "id": p["id"],
                        "category": p["category"],
                        "preference_text": p["preference_text"],
                    }
                    for p in previous_preferences
                ],
                ensure_ascii=False,
            )
        )
    user_parts.append("\n新对话片段：")
    user_parts.append(_format_conversation(conversation))
    user = "\n".join(user_parts)

    from pydantic import BaseModel, Field

    class _PrefOut(BaseModel):
        category: str
        preference_text: str
        confidence: float = Field(default=0.8, ge=0.0, le=1.0)
        supersedes_id: int | None = None

    class _PrefList(BaseModel):
        preferences: list[_PrefOut] = Field(default_factory=list)

    raw = await model.structured(
        system=sys_prompt,
        user=user,
        schema=_PrefList,
        temperature=0.1,
    )
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    out: list[NewPreference] = []
    for item in raw.get("preferences", []) or []:
        cat = item.get("category")
        if cat not in VALID_CATEGORIES:
            continue
        if float(item.get("confidence", 0.0)) < 0.6:
            continue
        out.append(
            NewPreference(
                category=cat,
                preference_text=str(item.get("preference_text") or "").strip(),
                confidence=float(item.get("confidence") or 0.0),
                supersedes_id=item.get("supersedes_id"),
            )
        )
    return out
