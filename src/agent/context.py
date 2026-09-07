"""History context rendering — pure prose serializer for intent injection .

Renders a prior run's persisted envelope (the ``{report, candidates, intent}``
shape returned by ``persistence.get_run``) into a prose outline that the
intent LLM can ground against when a user asks a follow-up like "再便宜的
呢". Pure function: no LLM, no IO, no state mutation. Empty fields degrade
gracefully (line omitted), so legacy rows with missing candidates/intent
render a partial but non-broken context.
"""
from __future__ import annotations

from typing import Any

_DECISION_LABELS = {"go": "推荐", "caution": "可选", "no-go": "不推荐"}

_DIM_LABELS = {
    "market_demand": "市场需求",
    "competition": "竞争度",
    "profit": "利润空间",
    "seasonality": "季节性",
    "repurchase": "复购率",
}


def render_history_context(
    envelope: dict[str, Any],
    created_at: str | None = None,
) -> str:
    """Render one envelope as a prose outline for intent-prompt injection.

    Sections (in order): header (+ optional timestamp), query, intent fields,
    decision label, market summary, per-candidate (name/score/rec/five
    dimensions/opportunities/risks). Empty fields are omitted silently.
    """
    parts: list[str] = ["## 历史上下文"]
    if created_at:
        parts.append(f"({created_at})")

    query = (envelope.get("query") or "").strip()
    if query:
        parts.append(f"用户查询: {query}")

    intent = envelope.get("intent") if isinstance(envelope.get("intent"), dict) else {}
    intent_lines: list[str] = []
    seed = (intent.get("seed_keyword") or "").strip()
    if seed:
        intent_lines.append(f"关键词 {seed}")
    if intent.get("budget_cny") is not None:
        intent_lines.append(f"预算 ¥{intent['budget_cny']:g}")
    prefs = intent.get("preferences") or []
    if prefs:
        intent_lines.append("偏好 " + "、".join(prefs))
    cat = (intent.get("category_hint") or "").strip()
    if cat:
        intent_lines.append(f"品类 {cat}")
    rationale = (intent.get("rationale") or "").strip()
    if rationale:
        intent_lines.append(f"理由 {rationale}")
    if intent_lines:
        parts.append("意图: " + " · ".join(intent_lines))

    decision = envelope.get("decision") or ""
    if decision:
        label = _DECISION_LABELS.get(decision, decision)
        parts.append(f"决策: {label}")

    report = envelope.get("report") if isinstance(envelope.get("report"), dict) else {}
    market_summary = (report.get("market_summary") or "").strip()
    if market_summary:
        parts.append(f"市场概述: {market_summary}")

    candidates = envelope.get("candidates") or []
    for i, c in enumerate(candidates, 1):
        if not isinstance(c, dict):
            continue
        name = c.get("name_cn") or c.get("name_en") or c.get("product_id") or "?"
        total = c.get("total_score")
        rec = c.get("recommendation") or ""
        head = f"候选 {i}: {name}"
        if isinstance(total, (int, float)):
            head += f" (总分 {total:.1f})"
        if rec:
            head += f" [{rec}]"
        parts.append(head)

        for s in c.get("scores") or []:
            if not isinstance(s, dict):
                continue
            dim = _DIM_LABELS.get(s.get("dimension", ""), s.get("dimension", "?"))
            score = s.get("score")
            reason = (s.get("reason") or "").strip()
            if isinstance(score, (int, float)):
                parts.append(f"  - {dim}: {score:.1f} — {reason}")
        opps = c.get("opportunities") or []
        if opps:
            parts.append("  机会: " + "; ".join(str(x) for x in opps))
        risks = c.get("risks") or []
        if risks:
            parts.append("  风险: " + "; ".join(str(x) for x in risks))

    return "\n".join(parts)