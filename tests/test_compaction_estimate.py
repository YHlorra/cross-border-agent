"""token 估算 SEAM（pure，no LLM）。

针对中文为主场景校验 chars/1.6 系数 + 混合法（usage 锚点 + trailing）。
"""

from __future__ import annotations

from agent.compaction.estimate import (
    _CHARS_PER_TOKEN,
    estimate_context_tokens,
    estimate_messages_tokens,
    estimate_tokens,
)


def test_chinese_text_chars_1_6():
    # 100 中文字符 ≈ 62 token
    text = "宠物用品" * 25  # 100 chars (4 codepoints × 25)
    n = estimate_tokens({"event_type": "user_message", "payload": {"text": text}})
    expected = max(1, round(100 / _CHARS_PER_TOKEN))
    assert n == expected
    assert n > 0


def test_short_message_minimum_one_token():
    n = estimate_tokens({"event_type": "user_message", "payload": {"text": "a"}})
    assert n == 1  # floor


def test_empty_payload_zero_tokens():
    assert estimate_tokens({"event_type": "user_message", "payload": {}}) == 0


def test_summary_event_only_counts_summary():
    payload = {
        "summary": "## Goal\nFind a cat food.\n## Critical Context\nvendor X is good",
        "noise": "ignored" * 100,
    }
    n = estimate_tokens({"event_type": "compaction", "payload": payload})
    expected = max(1, round(len(payload["summary"]) / _CHARS_PER_TOKEN))
    assert n == expected


def test_tool_call_args_counted_in_assistant():
    payload = {
        "text": "go",
        "tool_calls": [
            {"name": "LocalJSONAdapter.search_1688", "args": {"q": "猫粮", "page": 1}},
        ],
    }
    n_with_tool = estimate_tokens({"event_type": "tool_call", "payload": payload})
    n_without = estimate_tokens({"event_type": "tool_call", "payload": {"text": "go"}})
    assert n_with_tool > n_without


def test_estimate_messages_tokens_sums():
    events = [
        {"event_type": "user_message", "payload": {"text": "a" * 100}},
        {"event_type": "user_message", "payload": {"text": "b" * 100}},
    ]
    assert estimate_messages_tokens(events) == estimate_tokens(events[0]) + estimate_tokens(events[1])


def test_estimate_context_tokens_no_usage_full_estimate():
    events = [
        {"event_type": "user_message", "payload": {"text": "x" * 160}},
        {"event_type": "node_end", "payload": {"summary": "y" * 160}},
    ]
    # No usage → full sum
    assert estimate_context_tokens(events) == estimate_messages_tokens(events)


def test_estimate_context_tokens_with_usage_anchor_plus_trailing():
    """混合法：usage 锚点是「最后一条 assistant」对应的 token 数，trailing 是
    锚点之后的消息。Anchor=idx0 → trailing=events[1:]。"""
    events = [
        {"event_type": "node_end", "payload": {"summary": "first"}},  # anchor @ idx0
        {"event_type": "user_message", "payload": {"text": "a" * 160}},
    ]
    usage = {"total_tokens": 100}
    out = estimate_context_tokens(events, last_assistant_usage=usage)
    expected = 100 + estimate_messages_tokens(events[1:])  # user_message at idx1
    assert out == expected


def test_estimate_context_tokens_anchor_at_last_event_no_trailing():
    events = [
        {"event_type": "user_message", "payload": {"text": "x" * 160}},
        {"event_type": "node_end", "payload": {"summary": "final"}},  # last assistant
    ]
    usage = {"total_tokens": 100}
    out = estimate_context_tokens(events, last_assistant_usage=usage)
    # Anchor=idx1 (last); trailing=[] → result = usage only
    assert out == 100


def test_estimate_context_tokens_zero_usage_falls_back():
    events = [
        {"event_type": "user_message", "payload": {"text": "x" * 160}},
    ]
    out = estimate_context_tokens(events, last_assistant_usage={"total_tokens": 0})
    assert out == estimate_messages_tokens(events)


def test_estimate_context_tokens_empty_events():
    assert estimate_context_tokens([]) == 0


def test_estimate_accepts_event_row_or_payload():
    """Either full event row (with event_type) or raw payload dict."""
    row = {"event_type": "user_message", "payload": {"text": "x" * 32}}
    p_only = {"text": "x" * 32}
    # row form is slightly larger because event_type hint isn't used
    # but the text content is the same — should be equal
    assert estimate_tokens(row) == estimate_tokens(p_only)
