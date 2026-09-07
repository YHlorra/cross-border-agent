"""切点算法（采纳 findCutPoint，无 split-turn 双摘要）。

约束：
- 只在 user / assistant / report_chunk / final / empty / error / user_message
  事件处切（**绝不在 toolResult 切**——必须跟在它的 toolCall 后）
- compaction 事件自身是有效切点（这是「二次压缩」入口，但本项目
  阶段默认关闭，三重防御在 prepare 层处理）

主算法：reverse 累加 estimate_tokens，累计 ≥ keep_recent_tokens 时取
「最后一个有效切点」。
"""
from __future__ import annotations

from .estimate import estimate_tokens

# 允许作为切点的事件类型；tool_result 绝不切，tool_call 是
# assistant 消息的一部分，可切（CUTPOINT_ALLOWED 包含 tool_call）。
CUTPOINT_ALLOWED: frozenset[str] = frozenset(
    {
        "user_message",
        "node_start",
        "node_end",
        "tool_call",
        "report_chunk",
        "final",
        "empty",
        "error",
    }
)


def find_valid_cutpoints(
    events: list[dict],
    start: int,
    end: int,
) -> list[int]:
    """返回 ``[start, end)`` 范围内可用作切点的索引集合。"""
    out: list[int] = []
    for i in range(start, end):
        if not isinstance(events[i], dict):
            continue
        if events[i].get("event_type") in CUTPOINT_ALLOWED:
            out.append(i)
    return out


def find_cut_point(
    events: list[dict],
    start: int,
    end: int,
    keep_recent_tokens: int,
) -> int:
    """从 end 反向累加 token，定位「保留侧」边界。

    返回切点索引 i，[start, i) 摘要，[i, end) 保留。
    """
    cutpoints = find_valid_cutpoints(events, start, end)
    if not cutpoints:
        return end  # fallback：装不下任何东西就保留全部

    accumulated = 0
    for i in range(end - 1, start - 1, -1):
        if not isinstance(events[i], dict):
            continue
        if events[i].get("event_type") not in CUTPOINT_ALLOWED:
            continue
        accumulated += estimate_tokens(events[i])
        if accumulated >= keep_recent_tokens:
            # 取第一个 ≥ 当前 i 的切点
            for cp in cutpoints:
                if cp >= i:
                    return cp
            return cutpoints[0]

    return cutpoints[0]  # 预算装不下任何东西，从第一个切点开始
