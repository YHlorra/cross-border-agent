"""压缩触发策略（dynamic per-model window）。

核心判定：
    enabled && tokens > context_window - reserve_tokens

context_window 来自 ``CompactionSettings.effective_window(model_name)``。
"""
from __future__ import annotations

from .types import CompactionSettings


def should_compact(
    *,
    context_tokens: int,
    context_window: int,
    settings: CompactionSettings,
) -> bool:
    """是否应触发压缩。

    Args:
        context_tokens: 当前对话的真实 token 估算（混合法见 estimate.py）
        context_window: 实际窗口大小（per-model 查表 + fallback）
        settings: 压缩设置（enabled / reserve_tokens 等）

    Returns:
        True if compaction should fire.
    """
    if not settings.enabled:
        return False
    if context_window <= 0:
        return False  # 未知窗口 → 保守不触发
    if context_tokens <= 0:
        return False
    return context_tokens > context_window - settings.reserve_tokens
