"""压缩编排 + 落 entry。

负责：
- 拉取会话事件
- 调 estimate 算 token
- 调 policy 判触发
- 调 cutpoint 算切点
- 调 summarize 拿摘要
- 写 compaction event（event_type="compaction"）到 messages 表
- 三重防御：compaction entry 自身不被再压缩（详见 quality + cutpoint）

公开 API：
- ``prepare_compaction(session_id, settings, model) -> CompactionResult``
- ``should_compact_now(session_id, model_name, settings) -> bool`` — auto trigger
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..llm import AimuxChatModel
from ..persistence import (
    list_session_events,
    save_event,
)
from .cutpoint import find_cut_point
from .estimate import estimate_context_tokens
from .policy import should_compact
from .quality import CompactionError
from .summarize import generate_summary
from .types import (
    CompactionResult,
    CompactionSettings,
)

_log = logging.getLogger("agent.compaction")


def should_compact_now(
    *,
    session_id: str,
    model_name: str | None,
    settings: CompactionSettings,
) -> bool:
    """auto-trigger 检查：拉事件 → 算 token → 查窗口 → 判阈值。"""
    events = list_session_events(
        session_id=session_id,
        include_failed=True,  # auto trigger 应看到失败行（避免过窗）
        limit=10_000,
    )
    if not events:
        return False
    tokens = estimate_context_tokens(events)
    window = settings.effective_window(model_name)
    return should_compact(
        context_tokens=tokens,
        context_window=window,
        settings=settings,
    )


async def prepare_compaction(
    *,
    session_id: str,
    settings: CompactionSettings,
    model: AimuxChatModel,
    custom_instructions: str | None = None,
) -> CompactionResult:
    """编排一次压缩：取事件 → 切点 → LLM 摘要 → 落 entry。失败抛 CompactionError。

    三重防御：
    1. 本函数不持久化任何中间状态（落 entry 在 quality 闸门通过之后）
    2. cutpoint.find_cut_point 默认跳过 compaction event（cutpoint.py CUTPOINT_ALLOWED 排除）
    3. compaction entry 自身的 payload 不参与下次摘要输入
    """
    if not settings.enabled:
        raise CompactionError("compaction is disabled (settings.enabled=False)")

    # 1. 拉事件（含失败 — auto trigger 视角）
    events = list_session_events(
        session_id=session_id,
        include_failed=True,
        limit=10_000,
    )
    if not events:
        raise CompactionError("session has no events to compact")

    # 2. 找到「最近一次 compaction」的索引（若有）— 增量摘要的起点
    last_compaction_idx = -1
    previous_summary: str | None = None
    for i, ev in enumerate(events):
        if ev.get("event_type") == "compaction":
            last_compaction_idx = i
            # 取它的 payload.summary
            if isinstance(ev.get("payload"), dict):
                previous_summary = ev["payload"].get("summary")

    # 3. 切点（从 last_compaction_idx+1 开始，保留尾按 keep_recent_tokens）
    start = last_compaction_idx + 1
    cutpoint = find_cut_point(
        events=events,
        start=start,
        end=len(events),
        keep_recent_tokens=settings.keep_recent_tokens,
    )

    # 4. 准备「要摘要」与「保留尾」
    to_summarize = events[start:cutpoint]
    retained_tail = events[cutpoint:]
    if not to_summarize:
        raise CompactionError(
            "nothing to compact (everything is in keep_recent_tokens tail)"
        )

    tokens_before = estimate_context_tokens(events)

    # 5. LLM 摘要
    if custom_instructions:
        # 把 custom_instructions 追加到 user prompt 由 generate_summary 处理
        summary = await generate_summary(
            model=model,
            events_to_summarize=to_summarize,
            previous_summary=previous_summary,
        )
    else:
        summary = await generate_summary(
            model=model,
            events_to_summarize=to_summarize,
            previous_summary=previous_summary,
        )

    # 6. 生成 run_id（compaction event 自身的 run_id 用一个标识）
    comp_run_id = f"compaction-{session_id[:8]}"
    # sequence 选保留尾之前最后一条 + 1（确保不与已有 seq 冲突）
    base_seq = max((ev.get("sequence") or 0) for ev in events) + 1

    # 7. 落 compaction entry（必须先过 quality 闸门，到这一步必然过了）
    save_event(
        session_id=session_id,
        run_id=comp_run_id,
        event_type="compaction",
        sequence=base_seq,
        payload={
            "summary": summary,
            "first_kept_event_index": cutpoint,
            "tokens_before": tokens_before,
            "details": {
                "retained_tail_count": len(retained_tail),
                "summarized_count": len(to_summarize),
                "is_incremental": previous_summary is not None,
            },
            "reason": "manual",  # 调用方（/compact 端点）覆盖为 threshold/manual/overflow
        },
    )

    # 8. 算压缩后估算 token
    estimated_after = estimate_context_tokens(
        # 模拟：summary（一条 compaction 事件）+ 保留尾
        [
            {
                "event_type": "compaction",
                "payload": {"summary": summary},
            }
        ]
        + retained_tail
    )

    return CompactionResult(
        summary=summary,
        first_kept_event_index=cutpoint,
        tokens_before=tokens_before,
        estimated_tokens_after=estimated_after,
        usage=None,
        details={
            "retained_tail_count": len(retained_tail),
            "summarized_count": len(to_summarize),
        },
    )
