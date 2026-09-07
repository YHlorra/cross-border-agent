"""切点算法 SEAM（pure，no LLM）。


- 只在 user_message / node_start / node_end / report_chunk / final / empty / error 切
- tool_result 绝不在切
- reverse 累加 token 找保留尾边界
"""

from __future__ import annotations

from agent.compaction.cutpoint import find_cut_point, find_valid_cutpoints


def _ev(idx: int, et: str, text_chars: int = 50) -> dict:
    """Mock event row with given type and text length (for token count)."""
    payload: dict = {}
    if et == "user_message":
        payload["text"] = "x" * text_chars
    elif et == "node_end":
        payload["summary"] = "x" * text_chars
    elif et == "final":
        payload["summary"] = "x" * text_chars
    elif et == "tool_result":
        payload["content"] = "x" * text_chars
    else:
        payload["text"] = "x" * text_chars
    return {"event_type": et, "payload": payload, "sequence": idx}


def test_find_valid_cutpoints_excludes_tool_result():
    events = [
        _ev(0, "user_message"),
        _ev(1, "tool_call", text_chars=10),
        _ev(2, "tool_result", text_chars=500),  # never a cutpoint
        _ev(3, "final"),
    ]
    cuts = find_valid_cutpoints(events, 0, 4)
    assert cuts == [0, 1, 3]  # tool_result @ 2 excluded


def test_find_valid_cutpoints_empty_range():
    events = [_ev(0, "user_message"), _ev(1, "node_end")]
    assert find_valid_cutpoints(events, 5, 5) == []


def test_find_cut_point_basic_reverse_fill():
    """2 events, 50 chars each, keep_recent=10 → ~6 chars/token → need ≥10 token ≈ 16 chars → take last event."""
    events = [
        _ev(0, "user_message", text_chars=50),
        _ev(1, "node_end", text_chars=50),
    ]
    # Each event ~50/1.6 = 31 token. keep_recent=10 → from end accumulate 31, already ≥10 → cut at end-1=1
    cut = find_cut_point(events, 0, 2, keep_recent_tokens=10)
    # The cutpoint must be in [0, 1] (valid cutpoints). end-1=1 is valid → returns 1.
    assert cut in (0, 1)


def test_find_cut_point_budget_larger_than_total_keeps_everything():
    """如果保留预算大于总 token，不摘要（全保留）→ 切点=第一个 cutpoint。"""
    events = [
        _ev(0, "user_message", text_chars=100),
        _ev(1, "node_end", text_chars=100),
    ]
    # total ~125 token, keep 1000 → from end accumulate 62 (1), 62+62=125 (0); only crosses at 0
    # 实际上 reverse 累加 100/1.6≈62.5 → rounding 63 in token; from end 1→63 ≥1000? no
    # 0→63+63=126 ≥1000? no
    # Falls through to cutpoints[0]=0 (the first valid cutpoint)
    cut = find_cut_point(events, 0, 2, keep_recent_tokens=1000)
    # No cutpoint ever accumulates enough; returns cutpoints[0]
    assert cut == 0  # first valid cutpoint → keep everything (= nothing to summarize)


def test_find_cut_point_empty_valid_cuts_falls_back_to_end():
    """全部是 tool_result → 没有 valid cutpoint → 切点 = end (即全保留 = 0 摘要)"""
    events = [
        _ev(0, "tool_result", text_chars=100),
        _ev(1, "tool_result", text_chars=100),
    ]
    assert find_valid_cutpoints(events, 0, 2) == []
    cut = find_cut_point(events, 0, 2, keep_recent_tokens=10)
    # 返回 end（=2）→ 保留所有（= 0 摘要）
    assert cut == 2


def test_find_cut_point_skips_tool_result_keeps_user_message():
    events = [
        _ev(0, "user_message", text_chars=50),
        _ev(1, "tool_call", text_chars=10),
        _ev(2, "tool_result", text_chars=1000),  # huge but can't cut here
        _ev(3, "user_message", text_chars=50),
        _ev(4, "final", text_chars=50),
    ]
    # Valid cutpoints: [0, 1, 3, 4] (skip 2 = tool_result)
    cuts = find_valid_cutpoints(events, 0, 5)
    assert cuts == [0, 1, 3, 4]
    # Big keep budget → cutpoints[0]=0 → no summarize
    cut = find_cut_point(events, 0, 5, keep_recent_tokens=1000)
    assert cut == 0
