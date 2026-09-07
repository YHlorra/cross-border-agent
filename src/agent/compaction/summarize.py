"""LLM 摘要生成（增量摘要 + 摘要 prompt）。

只暴露 ``generate_summary``（LLM 调用 + 解析 + 闸门 + 序列化历史）。
prepare / append 逻辑在 ``prepare.py``。

initial vs incremental 唯一开关 = ``previous_summary`` 是否存在。
"""
from __future__ import annotations

import json
from typing import Any

from ..llm import AimuxChatModel
from ..prompts import load_prompt
from .estimate import estimate_tokens
from .quality import CompactionError, validate_summary

_INITIAL_PROMPT = """你是上下文摘要助手。阅读以下对话，生成结构化摘要。约束：
- 只输出摘要，不要续写对话、不要回答问题
- 不要调用任何工具
- 使用以下 sections（保持标题精确）：
  ## Goal
  ## Constraints & Preferences
  ## Progress (Done / In Progress / Blocked 用 [x]/[ ]/- 标记)
  ## Key Decisions
  ## Next Steps
- 每节精简；保留精确的字段名、错误信息
- 末尾附：## Critical Context（代码路径、API 名、错误码等不可丢失信息）
- JSON 输出：{"summary": "## Goal\\n...\\n## Critical Context\\n..."}
"""

_INCREMENTAL_PROMPT = """你是上下文摘要助手。整合旧摘要与新对话片段，生成更新版摘要。约束：
- 只输出摘要，不要续写对话、不要回答问题
- 不要调用任何工具
- PRESERVE 旧摘要中仍有效的信息
- ADD 新对话中新增的事实
- 用 Progress 勾选反映当前状态（[x] 完成 / [ ] 待办 / - 阻塞）
- 删除被新信息覆盖的失效项
- 保留 sections：## Goal / ## Constraints & Preferences / ## Progress / ## Key Decisions / ## Next Steps / ## Critical Context
- JSON 输出：{"summary": "## Goal\\n...\\n## Critical Context\\n..."}
"""


def _format_conversation(events: list[dict]) -> str:
    """把 wire 事件序列化成纯文本（截断每条 tool_result 2000 字符防模型续写）。"""
    lines: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        et = ev.get("event_type", "?")
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        text = payload.get("text") or payload.get("content") or payload.get("delta") or ""
        if isinstance(text, str) and len(text) > 2_000:
            text = text[:2_000] + "\n... [truncated 2000 chars]"
        if text:
            lines.append(f"[{et}] {text}")
        if payload.get("tool"):
            lines.append(
                f"  tool: {payload['tool']} args={json.dumps(payload.get('args') or {}, ensure_ascii=False)[:500]}"
            )
    return "\n".join(lines)


async def generate_summary(
    *,
    model: AimuxChatModel,
    events_to_summarize: list[dict],
    previous_summary: str | None = None,
    max_tokens: int = 4_000,
) -> str:
    """调用 LLM 生成结构化摘要。返回校验通过的 summary 文本。

    失败抛 ``CompactionError``（quality 模块定义）。
    """
    if not events_to_summarize:
        raise CompactionError("no events to summarize")

    is_incremental = previous_summary is not None
    system = _INCREMENTAL_PROMPT if is_incremental else _INITIAL_PROMPT

    conversation = _format_conversation(events_to_summarize)
    if is_incremental:
        user = f"""旧摘要：
{previous_summary}

新增对话片段：
{conversation}

输出更新后的 JSON 摘要。"""
    else:
        user = f"""对话内容：
{conversation}

输出 JSON 摘要。"""

    # 摘要调用走 LLM（structured 模式拿 JSON 字典）
    from pydantic import BaseModel

    class _SummaryOut(BaseModel):
        summary: str

    raw = await model.structured(
        system=system,
        user=user,
        schema=_SummaryOut,
        temperature=0.2,
    )
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    # 闸门
    return validate_summary(raw)
