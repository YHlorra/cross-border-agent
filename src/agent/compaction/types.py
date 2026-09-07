"""压缩机制类型 + 默认设置（  采纳）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CompactionSettings:
    """Per-stage 压缩设置（可来自 model_config.compaction 段或 DEFAULT 降级）。

    ``context_window`` 是「当前 agent 实际用的模型」窗口大小（按模型名
    查表 + fallback）。`reserve_tokens` 给摘要 prompt + 输出预留。
    `keep_recent_tokens` 是「保留尾」的绝对 token 预算
    同款语义）。
    """

    enabled: bool = False
    context_window: int = 32_000
    reserve_tokens: int = 8_192
    keep_recent_tokens: int = 4_096
    fallback_context_window: int = 32_000
    per_model_context_windows: dict[str, int] = field(default_factory=dict)

    def effective_window(self, model_name: str | None) -> int:
        """按模型名查 context_window；缺失走 fallback。"""
        if model_name and model_name in self.per_model_context_windows:
            return self.per_model_context_windows[model_name]
        return self.context_window or self.fallback_context_window


DEFAULT_COMPACTION_SETTINGS = CompactionSettings()


@dataclass(frozen=True)
class CompactionPreparation:
    """``prepare_compaction`` 产出：要摘要的事件 + 保留尾边界 + 切点索引。"""

    session_id: str
    run_id: str
    boundary_start: int  # [0, boundary_start) 的事件要被摘要
    boundary_end: int  # [boundary_end, len(events)) 也要保留（=cutpoint 之后）
    wire_events: list[dict]  # the actual event rows to summarize (full dicts)
    retained_tail: list[dict]  # boundary_end 之下的近端原始事件
    tokens_before: int  # 压缩前估算
    settings: CompactionSettings
    previous_summary: str | None  # 增量模式


@dataclass
class CompactionResult:
    """``append_compaction`` 产出：已落盘的行（也用于事件回放 + 状态机）。"""

    summary: str
    first_kept_event_index: int
    tokens_before: int
    estimated_tokens_after: int
    usage: dict[str, Any] | None = None
    details: dict[str, Any] | None = None
