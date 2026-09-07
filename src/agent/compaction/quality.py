"""摘要质量闸门（摘要质量不达标不写 entry）。

两道闸门：
1. 摘要非空且长度合理（拒绝空 / 截断）
2. 摘要响应里**不能出现 tool_call**（拒绝角色扮演续写）

通过 → 落 compaction entry；不通过 → 抛 CompactionError，外层 catch
后只发 `compaction_end { result: undefined, errorMessage }`，**不写**
messages 表（失败不污染 transcript 的契约）。
"""
from __future__ import annotations

import json
from typing import Any


class CompactionError(ValueError):
    """Summary quality gate rejected the response (do NOT persist)."""


def validate_summary(raw_response: str | dict[str, Any] | Any) -> str:
    """校验 LLM 摘要响应；返回校验通过的 summary 文本。

    失败抛 CompactionError。
    """
    # 1. 提取 summary 文本
    if isinstance(raw_response, str):
        summary_text = raw_response.strip()
    elif isinstance(raw_response, dict):
        if raw_response.get("summary"):
            summary_text = str(raw_response["summary"]).strip()
        elif raw_response.get("text"):
            summary_text = str(raw_response["text"]).strip()
        else:
            # 尝试从 payload 里提取
            summary_text = json.dumps(raw_response, ensure_ascii=False).strip()
    else:
        # Pydantic BaseModel
        s = getattr(raw_response, "summary", None) or getattr(
            raw_response, "text", None
        )
        if s:
            summary_text = str(s).strip()
        else:
            raise CompactionError(
                f"summary field missing in response: {raw_response!r}"
            )

    # 2. 非空 + 长度（防 length 截断）
    if not summary_text:
        raise CompactionError("summary is empty")
    if len(summary_text) < 16:
        raise CompactionError(
            f"summary suspiciously short ({len(summary_text)} chars): {summary_text!r}"
        )

    # 3. 拒绝 tool_call（角色扮演续写防御）
    raw_text = (
        raw_response
        if isinstance(raw_response, str)
        else (
            raw_response if isinstance(raw_response, str)
            else json.dumps(raw_response, ensure_ascii=False)
        )
    )
    if _contains_tool_call_marker(raw_text):
        raise CompactionError("summary response contains tool_call — model role-played")

    return summary_text


def _contains_tool_call_marker(text: str) -> bool:
    """检测摘要响应里是否含 tool_call 痕迹。"""
    lowered = text.lower()
    if '"tool_call"' in lowered or '"toolcall"' in lowered:
        return True
    if "function_call" in lowered and "arguments" in lowered:
        return True
    return False
