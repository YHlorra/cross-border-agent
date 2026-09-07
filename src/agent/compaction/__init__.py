"""会话上下文压缩模块。

dynamic per-model window（不用固定
百分比）、阈值/手动双入口、LLM 增量摘要、保留尾、三重防
御、失败不写 entry。

公开 API（外部接入点）：
- ``policy.should_compact(tokens, window, settings)`` — 阈值判定
- ``estimate.estimate_messages_tokens(events)`` — 真实 token 估算
- ``estimate.estimate_context_tokens(events, last_assistant_usage)`` — 混合
- ``cutpoint.find_cut_point(...)`` — 切点选择
- ``prepare.prepare_compaction(session_id, settings)`` — 编排 + 落 entry
- ``summarize.generate_summary(...)`` — LLM 摘要（实际调用）
- ``quality.validate_summary(...)`` — 摘要落盘前闸门
"""
