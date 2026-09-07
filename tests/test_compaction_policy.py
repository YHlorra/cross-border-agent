"""压缩策略 SEAM（pure，no LLM）。"""

from __future__ import annotations

from agent.compaction.policy import should_compact
from agent.compaction.types import CompactionSettings


def test_disabled_never_triggers():
    s = CompactionSettings(enabled=False, context_window=32_000, reserve_tokens=8_192)
    assert not should_compact(context_tokens=100_000, context_window=32_000, settings=s)


def test_unknown_window_never_triggers():
    s = CompactionSettings(enabled=True, context_window=0, reserve_tokens=8_192)
    assert not should_compact(context_tokens=50_000, context_window=0, settings=s)


def test_zero_tokens_never_triggers():
    s = CompactionSettings(enabled=True, context_window=32_000, reserve_tokens=8_192)
    assert not should_compact(context_tokens=0, context_window=32_000, settings=s)


def test_below_threshold_no_trigger():
    s = CompactionSettings(enabled=True, context_window=32_000, reserve_tokens=8_192)
    # 32_000 - 8_192 = 23_808; 10_000 < 23_808 → not trigger
    assert not should_compact(context_tokens=10_000, context_window=32_000, settings=s)


def test_above_threshold_triggers():
    s = CompactionSettings(enabled=True, context_window=32_000, reserve_tokens=8_192)
    # 32_000 - 8_192 = 23_808; 25_000 > 23_808 → trigger
    assert should_compact(context_tokens=25_000, context_window=32_000, settings=s)


def test_at_threshold_no_trigger():
    s = CompactionSettings(enabled=True, context_window=32_000, reserve_tokens=8_192)
    # 32_000 - 8_192 = 23_808; 23_808 == 23_808 → not trigger (strict >)
    assert not should_compact(context_tokens=23_808, context_window=32_000, settings=s)


def test_modern_200k_window_with_low_reserve():
    """200K 模型 + 8K reserve: only when tokens > 191_808 trigger — never under modern usage."""
    s = CompactionSettings(enabled=True, context_window=200_000, reserve_tokens=8_192)
    # typical multi-turn 5K tokens — well under threshold
    assert not should_compact(context_tokens=5_000, context_window=200_000, settings=s)
    # just under threshold (191_808) — no trigger (strict >)
    assert not should_compact(context_tokens=191_000, context_window=200_000, settings=s)
    # just over threshold — trigger
    assert should_compact(context_tokens=192_000, context_window=200_000, settings=s)


def test_effective_window_per_model_lookup():
    s = CompactionSettings(
        enabled=True,
        context_window=32_000,
        per_model_context_windows={"gpt-4o": 128_000, "claude-3-5-sonnet-20241022": 200_000},
    )
    assert s.effective_window("gpt-4o") == 128_000
    assert s.effective_window("claude-3-5-sonnet-20241022") == 200_000
    assert s.effective_window("unknown-model") == 32_000  # fallback to context_window
    assert s.effective_window(None) == 32_000
