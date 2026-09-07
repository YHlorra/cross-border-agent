"""Live probe — aimux 原生 tool-calling（降级预案触发器）。

Gated on LLM_API_KEY（沿用 live 测试门控约定，缺 key 直接 skip）。
目标：证明 aimux 0.3.0 在真实 provider（MiniMax-M3）上：
  ① generate_text + FunctionTool → 返回 tool_calls 且 finish_reason=="tool-calls"
  ② stream_text 在工具调用场景产出 ToolInputStart/ToolCall StreamPart（含 json 参数）

若 ① 或 ② 失败即触发降级预案——改为实现手搓 loop
（aimux 原生直调），后续任务不变。探针刻意不依赖本仓库 agent 模块
（直接走 aimux wrapper），失败时能精确定位是"网关能力"还是"适配层"问题。
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


def _require_key() -> None:
    if not os.environ.get("LLM_API_KEY"):
        pytest.skip("live test skipped — missing LLM_API_KEY")


def _build_real_model():
    from agent.llm import AimuxChatModel, build_model_from_args
    from agent.providers_store import effective_agent_model, find_api_key, load_model_config

    entry = effective_agent_model(load_model_config(), "selection")
    provider = entry.get("provider")
    model_id = entry.get("model")
    if not provider or not model_id:
        pytest.skip("live test skipped — no selection model wired in model_config.json")
    model = build_model_from_args(
        provider_name=provider,
        api_key=find_api_key(provider) or os.environ.get("LLM_API_KEY"),
        model_id=model_id,
        base_url=entry.get("base_url") or None,
    )
    return AimuxChatModel(model)


def _echo_tool():
    from aimux.wrapper import FunctionTool

    return FunctionTool(
        name="echo_keyword",
        description="把给定的关键词原样返回。",
        input_schema={
            "type": "object",
            "properties": {"keyword": {"type": "string"}},
            "required": ["keyword"],
        },
    )


def test_probe_generate_text_returns_tool_calls() -> None:
    """① 非流式 tool-calling：真实模型必须返回结构化 tool_calls。"""
    _require_key()
    from aimux.wrapper import GenerateTextOptions, generate_text

    model = _build_real_model()
    opts = GenerateTextOptions(tools=[_echo_tool()], temperature=0.2)
    result = generate_text(
        model._model,
        [
            {"role": "system", "content": "你是探针。用户要求时调用 echo_keyword 工具。"},
            {"role": "user", "content": "请调用 echo_keyword，keyword=宠物用品"},
        ],
        opts,
    )
    assert result.tool_calls, f"no tool_calls returned; text={result.text[:200]!r}"
    tc = result.tool_calls[0]
    assert tc.tool_name == "echo_keyword"
    assert result.finish_reason.unified == "tool-calls"
    assert tc.input.get("keyword") == "宠物用品"


def test_probe_stream_text_emits_tool_input_parts() -> None:
    """② 流式 tool-calling：stream 必须产出 ToolInputStart/Delta 等 StreamPart。"""
    _require_key()
    from aimux.wrapper import GenerateTextOptions, stream_text

    model = _build_real_model()
    opts = GenerateTextOptions(tools=[_echo_tool()], temperature=0.2)
    parts = list(
        stream_text(
            model._model,
            [
                {"role": "system", "content": "你是探针。用户要求时调用 echo_keyword 工具。"},
                {"role": "user", "content": "请调用 echo_keyword，keyword=宠物用品"},
            ],
            opts,
        )
    )
    kinds = [next(iter(p)) for p in parts]
    assert any("ToolInput" in k for k in kinds), (
        f"no ToolInput* StreamPart; kinds={kinds}"
    )
    assert any(k == "ToolCall" for k in kinds), f"no ToolCall StreamPart; kinds={kinds}"
