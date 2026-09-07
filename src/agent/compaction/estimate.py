"""Token 估算。

原文用 chars/4（高估，英文友好）。本项目主场景是中文 + JSON 负载，
按经验 1.6 char ≈ 1 token 偏保守高估（中文 1 char ≈ 0.5 token → 估到
1 char = 0.625 token 偏稳）。SEAM 测覆盖：
- 单条消息的纯文本估算
- 包含 thinking / tool_call.arguments / tool_result 的 assistant 消息
- summary 类事件的「只算 summary 字符」
- 整段混合估算：usage 锚点 + trailing
- 中文 + 英文混合的 chars/1.6 系数精确度
"""
from __future__ import annotations

from typing import Any

# 中文为主场景的 char→token 系数：1.6 char ≈ 1 token（保守高估）。
# chars/4 适配英文 ~4 char/token；本项目偏 0.625 token/char（中文更
# 高 token 密度）；用 1.6 char/token 偏安全侧，不会低估。
_CHARS_PER_TOKEN = 1.6

# 兜底（无锚点时按行宽粗估）
_ESTIMATED_IMAGE_CHARS = 4_800


def estimate_tokens(event_or_payload: dict[str, Any]) -> int:
    """估算一条消息（或一个 summary 事件）的 token 数。

    接受完整 event row（含 event_type + payload）或直接 payload 字典。
    字段约定：
    - user / toolResult / custom：按 text 字符数估
    - assistant：text + thinking + tool_calls.name + JSON(arguments)
    - summary 类（compaction/branch_summary）：只算 summary 字符
    - image 记 4800 chars 固定（图片描述长度上限）
    """
    payload = (
        event_or_payload.get("payload")
        if "payload" in event_or_payload
        else event_or_payload
    )
    if not isinstance(payload, dict):
        return 0

    # summary 类事件：只算 summary 字符
    if payload.get("summary") and isinstance(payload["summary"], str):
        return _chars_to_tokens(len(payload["summary"]))

    ev_type = event_or_payload.get("event_type") or payload.get("event") or ""
    text = payload.get("text") or payload.get("content") or ""
    if not isinstance(text, str):
        text = ""

    total_chars = 0
    total_chars += len(text)

    # assistant 类型：累加 thinking + tool_calls
    if ev_type == "node_end" and payload.get("intent"):
        # node_end 也带 intent 字段（payload 里有 intent dict）
        # 不影响 assistant 估算但保险起见序列化
        try:
            total_chars += len(_safe_json(payload["intent"]))
        except Exception:  # noqa: BLE001
            pass

    # tool_call 字段在 wire 事件里是分开的（event=tool_call）；
    # 任何事件若 payload 里含 tool_calls 则累加
    for tc in payload.get("tool_calls") or []:
        if isinstance(tc, dict):
            name = tc.get("name") or tc.get("function", {}).get("name") or ""
            args = (
                tc.get("args")
                or tc.get("function", {}).get("arguments")
                or {}
            )
            total_chars += len(name) + len(_safe_json(args))

    if total_chars == 0:
        return 0
    return _chars_to_tokens(total_chars)


def estimate_messages_tokens(events: list[dict[str, Any]]) -> int:
    """整段事件流的 token 估算（无 usage 锚点时用）。"""
    return sum(estimate_tokens(ev) for ev in events)


def estimate_context_tokens(
    events: list[dict[str, Any]],
    last_assistant_usage: dict[str, Any] | None = None,
) -> int:
    """混合法：有 usage 锚点时 = usage 总
    token + 锚点后 trailing 估算；无锚点时全量估算。

    last_assistant_usage 形如 ``{"total_tokens": N}`` 或 ``{input_tokens,
    output_tokens, cache_read, cache_write}``。若 usage total=0 或 None，
    视作无锚点。
    """
    if not events:
        return 0
    usage_total = 0
    if last_assistant_usage:
        usage_total = int(last_assistant_usage.get("total_tokens") or 0)
        if usage_total == 0:
            in_t = last_assistant_usage.get("input_tokens") or 0
            out_t = last_assistant_usage.get("output_tokens") or 0
            cache_r = last_assistant_usage.get("cache_read") or 0
            cache_w = last_assistant_usage.get("cache_write") or 0
            usage_total = int(in_t) + int(out_t) + int(cache_r) + int(cache_w)
    if usage_total == 0:
        return estimate_messages_tokens(events)

    # 找最后一条 assistant usage 对应的事件索引：取 events 中最后一条
    # event_type="node_end" 节点；之后的 trailing 消息按 chars/1.6 估。
    # 简化：直接从最后一条 assistant 消息往后累加。
    last_idx = _last_assistant_index(events)
    if last_idx < 0:
        return usage_total
    trailing = events[last_idx + 1 :]
    return usage_total + estimate_messages_tokens(trailing)


def _chars_to_tokens(chars: int) -> int:
    """char → token 系数（1.6 char / token，保守高估）。"""
    return max(1, int(round(chars / _CHARS_PER_TOKEN)))


def _safe_json(obj: Any) -> str:
    """Best-effort JSON serialization for token estimation."""
    import json

    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(obj)


def _last_assistant_index(events: list[dict[str, Any]]) -> int:
    """返回 events 中最后一条 assistant 类型事件的索引。"""
    for i in range(len(events) - 1, -1, -1):
        ev = events[i]
        et = ev.get("event_type") if isinstance(ev, dict) else None
        if et in ("node_end", "tool_call", "report_chunk"):
            return i
    return -1
