"""render_history_context — SEAM tests .

Pure function: no LLM, no IO. The intent LLM sees this prose outline as
context, so regressions here directly corrupt follow-up quality. Pin:

- full envelope renders every persisted field
- empty / missing fields degrade gracefully (no broken lines)
- decision go / caution / no-go render the matching label
- five-dimension scores render with localized labels
- legacy envelope (bare report, no candidates/intent) renders cleanly
"""

from __future__ import annotations

from agent.context import render_history_context


def test_full_envelope_renders_every_field():
    envelope = {
        "query": "宠物用品",
        "intent": {
            "seed_keyword": "猫粮",
            "budget_cny": 2000.0,
            "preferences": ["low_price"],
            "category_hint": "宠物食品",
            "rationale": "季节性需求上升",
        },
        "decision": "go",
        "report": {"market_summary": "猫粮市场月搜索量 8k+，同比 +12%。"},
        "candidates": [
            {
                "name_cn": "高蛋白猫粮A",
                "total_score": 8.4,
                "recommendation": "go",
                "scores": [
                    {"dimension": "market_demand", "score": 9.0, "reason": "需求稳"},
                    {"dimension": "competition", "score": 7.5, "reason": "中度竞争"},
                    {"dimension": "profit", "score": 8.0, "reason": "毛利 35%"},
                ],
                "opportunities": ["搜索量上升", "复购高"],
                "risks": ["头部品牌垄断"],
            }
        ],
    }
    out = render_history_context(envelope, created_at="2026-08-31T10:00:00+00:00")

    # header + timestamp + query
    assert "## 历史上下文" in out
    assert "(2026-08-31T10:00:00+00:00)" in out
    assert "用户查询: 宠物用品" in out
    # intent: every field present, localized preference name allowed
    assert "关键词 猫粮" in out
    assert "预算 ¥2000" in out
    assert "偏好 low_price" in out
    assert "品类 宠物食品" in out
    assert "理由 季节性需求上升" in out
    # decision label
    assert "决策: 推荐" in out
    # market summary
    assert "市场概述: 猫粮市场月搜索量" in out
    # candidate block
    assert "候选 1: 高蛋白猫粮A" in out
    assert "总分 8.4" in out
    assert "[go]" in out
    # five dimensions with localized labels
    assert "市场需求: 9.0" in out
    assert "竞争度: 7.5" in out
    assert "利润空间: 8.0" in out
    # opps + risks
    assert "机会: 搜索量上升; 复购高" in out
    assert "风险: 头部品牌垄断" in out


def test_decision_labels_go_caution_no_go():
    for raw, label in [("go", "推荐"), ("caution", "可选"), ("no-go", "不推荐")]:
        out = render_history_context({"decision": raw})
        assert f"决策: {label}" in out


def test_empty_or_missing_fields_omit_lines():
    # No intent, no report, no candidates — only the header.
    out = render_history_context({})
    assert out == "## 历史上下文"

    # Empty intent dict is treated like missing — no intent section.
    out2 = render_history_context({"intent": {}, "decision": "", "candidates": []})
    assert out2 == "## 历史上下文"


def test_legacy_bare_report_envelope():
    """A legacy row stores the report dict directly (no ``report`` envelope
    wrapper). render must still surface market_summary from the bare report.
    """
    envelope = {
        "query": "旧查询",
        "decision": "go",
        "report": {
            "session_id": "t1",
            "seed_keyword": "kw",
            "market_summary": "legacy market",
            "top_recommendations": [],
        },
        "candidates": [],
        "intent": None,
    }
    out = render_history_context(envelope)
    assert "市场概述: legacy market" in out
    assert "决策: 推荐" in out


def test_non_dict_candidate_is_skipped():
    envelope = {
        "candidates": [
            "not a dict",
            {"name_cn": "猫粮B", "total_score": 6.0, "recommendation": "caution"},
        ]
    }
    out = render_history_context(envelope)
    assert "候选 1:" not in out  # skipped
    assert "候选 1: 猫粮B" in out or "候选 2: 猫粮B" in out
    assert "[caution]" in out


def test_scores_render_with_localized_labels():
    out = render_history_context(
        {
            "candidates": [
                {
                    "name_cn": "x",
                    "scores": [
                        {"dimension": "seasonality", "score": 6.0, "reason": "季节"},
                        {"dimension": "repurchase", "score": 8.0, "reason": "复购"},
                    ],
                }
            ]
        }
    )
    assert "季节性: 6.0" in out
    assert "复购率: 8.0" in out


def test_budget_format_no_trailing_zeros():
    out = render_history_context(
        {"intent": {"budget_cny": 2000.0, "preferences": []}}
    )
    # `:g` formatting collapses whole-number floats.
    assert "预算 ¥2000" in out
    assert "预算 ¥2000.0" not in out