"""摘要质量闸门 SEAM（pure，no LLM）。


- 拒绝空 / 截断 / 太短
- 拒绝 tool_call marker（角色扮演续写）
- 通过 → 返回清洗后的 summary 文本
"""

from __future__ import annotations

import pytest

from agent.compaction.quality import CompactionError, validate_summary


def test_empty_string_rejected():
    with pytest.raises(CompactionError, match="empty"):
        validate_summary("")


def test_whitespace_only_rejected():
    with pytest.raises(CompactionError, match="empty"):
        validate_summary("   \n\t  ")


def test_short_summary_rejected():
    with pytest.raises(CompactionError, match="suspiciously short"):
        validate_summary("too short")  # 9 chars


def test_minimum_length_accepted():
    s = "## Goal\nFind a cat food bowl."  # 28 chars
    assert validate_summary(s) == s


def test_dict_with_summary_accepted():
    payload = {"summary": "## Goal\nx" * 30}
    assert validate_summary(payload) == payload["summary"]


def test_dict_without_summary_falls_back_to_json():
    payload = {"text": "## Goal\n" + "y" * 30}
    out = validate_summary(payload)
    assert "Goal" in out


def test_dict_with_missing_keys_rejected():
    with pytest.raises(CompactionError):
        validate_summary({})


def test_tool_call_marker_rejected():
    summary = '## Goal\nFind a bowl\n\n```json\n{"tool_call": {"name": "x"}}\n```'
    with pytest.raises(CompactionError, match="tool_call"):
        validate_summary(summary)


def test_tool_call_marker_in_dict_rejected():
    payload = {"summary": '## Goal\nx' * 20, "tool_call": {"name": "x"}}
    with pytest.raises(CompactionError, match="tool_call"):
        validate_summary(payload)


def test_function_call_with_arguments_rejected():
    summary = '## Goal\nx' * 30 + '\nfunction_call with arguments {"q": "x"}'
    with pytest.raises(CompactionError, match="tool_call"):
        validate_summary(summary)


def test_normal_summary_accepted():
    summary = """## Goal
Find a cat food bowl.
## Constraints & Preferences
- Budget: ¥2000
## Progress
- [x] Found 5 candidates
## Key Decisions
- Use non-slip base
## Next Steps
- Generate Listing
## Critical Context
- vendor X: reliable
"""
    # validate_summary .strips the input; compare against stripped.
    assert validate_summary(summary) == summary.strip()
