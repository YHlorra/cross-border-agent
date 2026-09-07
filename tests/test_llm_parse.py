"""SEAM tests: extract_json_payload — pure text→text JSON extraction for structured calls.

Covers the shapes MiniMax actually returns (probe evidence, 2026-08-28):
think blocks, markdown fences, surrounding prose. No LLM involved — pure function.
"""
from __future__ import annotations

import pytest

from agent.llm import extract_json_payload


def test_plain_json_passthrough() -> None:
    assert extract_json_payload('{"keyword": "宠物用品"}') == '{"keyword": "宠物用品"}'


def test_strips_think_block_before_json() -> None:
    raw = '<think>\n让我按照规则提取：\n- keyword: 宠物用品\n</think>\n\n{"keyword": "宠物用品"}'
    assert extract_json_payload(raw) == '{"keyword": "宠物用品"}'


def test_strips_unclosed_think_tag() -> None:
    raw = '</think>\n{"keyword": "宠物用品"}'
    assert extract_json_payload(raw) == '{"keyword": "宠物用品"}'


def test_strips_markdown_code_fence() -> None:
    raw = '```json\n{"keyword": "宠物用品"}\n```'
    assert extract_json_payload(raw) == '{"keyword": "宠物用品"}'


def test_extracts_json_from_surrounding_prose() -> None:
    raw = '好的，提取结果如下：\n{"keyword": "宠物用品"}\n以上供参考。'
    assert extract_json_payload(raw) == '{"keyword": "宠物用品"}'


def test_nested_braces_kept_intact() -> None:
    raw = '<think>x</think>{"a": {"b": [1, 2]}, "c": 3}'
    assert extract_json_payload(raw) == '{"a": {"b": [1, 2]}, "c": 3}'


def test_brace_inside_string_not_treated_as_nesting() -> None:
    raw = '{"rationale": "预算 {2000} 元", "keyword": "宠物用品"}'
    assert extract_json_payload(raw) == '{"rationale": "预算 {2000} 元", "keyword": "宠物用品"}'


def test_no_json_raises_with_snippet() -> None:
    with pytest.raises(ValueError) as exc:
        extract_json_payload("抱歉，我无法处理这个请求。")
    assert "no JSON object" in str(exc.value)


def test_empty_input_raises() -> None:
    with pytest.raises(ValueError):
        extract_json_payload("")
