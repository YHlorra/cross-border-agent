"""Auto session title generation.

Cheap fire-and-forget LLM call: render the user's first message into a
≤16-char Chinese task title. Failures are silent — the caller treats
``None`` as "keep UUID fallback".
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..prompts import load_prompt
from ..llm import AimuxChatModel


class _TitleOutput(BaseModel):
    title: str = Field(..., description="≤16-char Chinese task title")


async def generate_session_title(
    model: AimuxChatModel,
    query: str,
    *,
    max_chars: int = 16,
) -> str | None:
    """Return a short Chinese title for the user's first message.

    Returns ``None`` on any failure (empty query, LLM error, empty/over-long
    title, or validation rejection). Callers should treat ``None`` as
    "skip writing; sidebar falls back to session_id slice".
    """
    if not query or not query.strip():
        return None

    _, system = load_prompt("title")
    user = query.strip()

    try:
        result = await model.structured(
            system=system,
            user=user,
            schema=_TitleOutput,
        )
    except Exception:  # noqa: BLE001
        return None

    title = (result.title or "").strip()
    # Strip trailing punctuation the model sometimes adds despite the rule.
    title = title.rstrip("。.!?！？,，;；:：")
    title = title.strip()
    if not title:
        return None
    if len(title) > max_chars:
        title = title[:max_chars].rstrip()
    if not title:
        return None
    return title
