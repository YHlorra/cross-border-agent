"""Listing agent assembly ( 3.1/3.2) — official create_agent for drafts.

The listing module's monolithic draft node becomes a create_agent run whose
model decides when to submit a draft and iterates through the SEAM validation
layer until it is clean:

- Tool: ``submit_draft`` — takes the raw Amazon content JSON (item_name /
  bullet_point / product_description / generic_keyword) and returns the
  auto-fixed output + issues + hard_failed after ``validate_listing``
  (validation contract — unchanged, never bypassed).
- The model sees candidate/market/seller context in the system prompt, and
  hard-fail issues are fed back so it can revise before finalizing.
- ``stream_run_listing_loop`` drives the loop and re-emits the listing wire
  dialect (node_start draft / listing_draft / final kind=listing) so the
  frontend listing card + session replay stay intact.

Handoff: the selection tool registry gains ``handoff_to_listing``
(``build_handoff_tool``); when the user's request carries listing intent the
selection model can call it with a chosen candidate. The driver records the
call and the frontend keeps its existing 转入 Listing affordance — the macro
layer stays the slash router, which already dispatches /listing to
this endpoint.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool as lc_tool

from ..llm import StartupConfigError, ThinkStreamFilter
from ..prompts import load_prompt
from ..state import ProductCandidate
from .events_middleware import EventsMiddleware

log = logging.getLogger(__name__)


def _candidate_payload(candidate: ProductCandidate | dict) -> dict:
    if isinstance(candidate, ProductCandidate):
        return candidate.model_dump()
    return dict(candidate)


def build_listing_tools() -> list[Any]:
    """Registry for the listing agent. ``submit_draft`` is the only LLM-facing
    tool — everything else is deterministic (the validation layer)."""

    @lc_tool
    def submit_draft(listing_json: str) -> str:
        """提交一版 Amazon Listing 草稿做合规校验。listing_json 必须是 JSON
        对象：{"item_name": str, "bullet_point": [5 条 str],
        "product_description": str, "generic_keyword": str}。
        返回 {"output": 自动修复后的成品, "issues": [...], "hard_failed": bool}；
        hard_failed=true 时按 issues 修改后重新提交。"""
        from ..listing.schema import ListingOutput, load_market_config
        from ..listing.validate import validate_listing

        try:
            raw = json.loads(listing_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"invalid listing_json: {e}"}, ensure_ascii=False)
        try:
            draft = ListingOutput.model_validate(raw)
        except Exception as e:  # noqa: BLE001 — surface shape problems honestly
            return json.dumps({"error": f"schema validation failed: {e}"}, ensure_ascii=False)
        market = load_market_config(code="US")
        result = validate_listing(draft, market)
        return json.dumps(
            {
                "output": result.output.model_dump(),
                "issues": [i.model_dump() for i in result.issues],
                "hard_failed": result.hard_failed,
            },
            ensure_ascii=False,
        )

    return [submit_draft] + _build_image_tools()


def _build_image_tools() -> list[Any]:
    """feature-wave-1 — 绘蛙图生成工具(无 HUIWA_API_KEY 时不注册)。"""
    from .image_tool import build_image_tools

    return build_image_tools()


def compose_listing_system_prompt(tools: list[Any]) -> str:
    """draft.md + 工作方式契约 (+ 生图 SOP skill 附录，仅当生图工具已注册)。

    SEAM 纯函数：prompt 组装的唯一决策点，测试直接调用。
    """
    _, body = load_prompt("draft", subdir="listing")
    prompt = (
        f"{body}\n\n"
        "## Agent 工作方式\n"
        "1. 用 submit_draft 提交完整草稿 JSON（item_name/bullet_point[5]"
        "/product_description/generic_keyword/product_type——product_type 用"
        "亚马逊产品类型关键词，大写下划线式，如 PET_FEEDER）。\n"
        "2. 收到 hard_failed=true 时按 issues 修改后再提交（最多 3 次）。\n"
        "3. 校验通过（hard_failed=false）后，输出一行最终确认："
        "`DRAFT_OK`，不要再提交。\n"
        "4. 候选数据在市场上下文里；搜索词禁止出现竞品品牌。\n"
        "5. product_type 必填——首次提交若漏写会被 hard_fail 报"
        "product_type_missing，按惯例从商品品类推断一个最贴近的亚马逊产品"
        "类型关键词（大写下划线式），不要写 human-readable 名。"
    )
    # 生图 skill 条件注入：仅当生图工具实际
    # 注册（HUIWA_API_KEY 存在）时才注入六要素方法论——没有生图工具时
    # 这份知识无用，省 system prompt 预算。
    if any(getattr(t, "name", "") == "generate_listing_image" for t in tools):
        _, imagegen_skill = load_prompt("ecom-imagegen", subdir="skills")
        prompt = f"{prompt}\n\n---\n\n# 附录：商品生图 SOP\n\n{imagegen_skill}"
    return prompt


def build_listing_agent(
    *,
    model: BaseChatModel,
    tools: Optional[list[Any]] = None,
    events_queue: Optional[asyncio.Queue] = None,
    middleware: Optional[list[AgentMiddleware]] = None,
    system_prompt: Optional[str] = None,
    name: str = "listing_agent",
) -> Any:
    """Compile the listing agent (prompt from prompts/listing/draft.md + a
    hard JSON contract section)."""
    if tools is None:
        tools = build_listing_tools()
    if system_prompt is None:
        system_prompt = compose_listing_system_prompt(tools)
    mw: list[AgentMiddleware] = list(middleware or [])
    if events_queue is not None:
        mw.append(EventsMiddleware(events_queue.put))
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=mw,
        name=name,
    )


async def stream_run_listing_loop(
    *,
    candidate: ProductCandidate | dict,
    market_code: str,
    brand: str,
    competitor_brands: list[str],
    session_id: str,
    run_id: str,
    agents_models: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """Drive one listing draft run through the create_agent loop; yields the
    listing wire dialect (node_start draft / listing_draft / final)."""
    model = agents_models.get("listing")
    if model is None:
        raise StartupConfigError(["listing model"])
    from langchain_core.language_models.chat_models import BaseChatModel as _BCM
    from ..llm import AimuxChatModel
    from ..llm_langchain import AimuxLangchainAdapter

    adapter_model = (
        model
        if isinstance(model, _BCM) and not isinstance(model, AimuxChatModel)
        else AimuxLangchainAdapter(model)
    )
    candidate_payload = _candidate_payload(candidate)
    market_block = (
        "市场约束(US): 标题≤75字符; 五点5条(每条10-255字符,结尾无标点); "
        "描述≤2000字符; 搜索词≤250字节"
    )
    user_payload = (
        "候选商品数据:\n"
        f"- 品名(英): {candidate_payload.get('name_en', '')}\n"
        f"- 品名(中): {candidate_payload.get('name_cn', '')}\n"
        f"- 品类: {candidate_payload.get('category', '')}\n"
        f"- 采购价: ¥{candidate_payload.get('source_price_cny', '')}\n"
        f"- 目标售价: ${candidate_payload.get('target_price_usd', '')}\n"
        f"- 月搜索量: {candidate_payload.get('monthly_search') or '未知'}\n"
        f"- 竞品品牌(搜索词禁用): {', '.join(competitor_brands) or '（无数据）'}\n"
        f"- 卖家品牌: {brand or '（未提供——不要在标题或文案中编造品牌名）'}\n"
        f"{market_block}\n\n请为这个商品生成 Listing。"
    )
    events_queue: asyncio.Queue = asyncio.Queue()
    agent = build_listing_agent(model=adapter_model, events_queue=events_queue)

    async def _drive() -> list[Any]:
        out = await agent.ainvoke({"messages": [HumanMessage(content=user_payload)]})
        return out["messages"]

    task = asyncio.create_task(_drive())
    try:
        yield {"event": "node_start", "node": "draft"}
        while True:
            try:
                ev = await asyncio.wait_for(events_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                if task.done():
                    break
                continue
            yield ev

        msgs = task.result()
        # The validated draft = the last submit_draft ToolMessage content (the
        # wire tool_result summary is truncated for display; the full JSON lives
        # in the message history).
        final_draft: dict | None = None
        hard_failed = False
        issues: list[dict] = []
        for m in reversed(msgs):
            if not isinstance(m, ToolMessage):
                continue
            if getattr(m, "name", None) != "submit_draft":
                continue
            if getattr(m, "status", "success") == "error":
                continue
            try:
                parsed = json.loads(m.content) if isinstance(m.content, str) else {}
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "output" in parsed:
                final_draft = parsed["output"]
                hard_failed = bool(parsed.get("hard_failed"))
                issues = list(parsed.get("issues") or [])
                break

        if final_draft is None:
            # the loop never produced a validated draft → honest error
            yield {
                "event": "error",
                "code": "NoListingDraft",
                "message": "listing agent 未产出合规草稿",
            }
            return

        yield {
            "event": "listing_draft",
            "listing": final_draft,
            "issues": issues,
            "hard_failed": hard_failed,
        }
        yield {
            "event": "final",
            "kind": "listing",
            "run_id": run_id,
            "product_id": candidate_payload.get("product_id", ""),
            "market_code": market_code,
            "listing": final_draft,
            "issues": issues,
            "hard_failed": hard_failed,
            "session_id": session_id,
        }
    finally:
        # Same turn-boundary cancel contract as stream_run_loop: a client
        # disconnect must not leave the listing loop running behind a dead
        # response (a full draft turn is 2-3 minutes of LLM spend).
        if not task.done():
            task.cancel()
            log.info("listing run %s cancelled by client", run_id)
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())


# ─── selection-side handoff tool (3.2) ─────────────────────────────────────


def build_handoff_tool() -> Any:
    """Tool the selection agent may call when the user's follow-up asks to go
    to listing. Records the choice deterministically; the selection driver
    can then route /listing (the frontend already exposes 转入 Listing)."""

    @lc_tool
    def handoff_to_listing(candidate_json: str) -> str:
        """把选品结果转入 Listing 生成。candidate_json: 候选商品 JSON（含
        product_id/name_cn/name_en/target_price_usd/category…——直接引用
        score_candidates 或 match_candidates 返回里的某个对象，不要改写）。
        调用后输出一句确认即可（例如「已转入 Listing 生成」），不要继续调用
        其它选品工具。"""
        try:
            payload = json.loads(candidate_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"invalid candidate_json: {e}"}, ensure_ascii=False)
        required = {"product_id", "name_cn", "name_en", "target_price_usd"}
        missing = [k for k in required if k not in payload]
        if missing:
            return json.dumps({"error": f"candidate missing: {missing}"}, ensure_ascii=False)
        return json.dumps({"handoff": "listing", "candidate": payload}, ensure_ascii=False)

    return handoff_to_listing


# ─── listing token-stream variant ────────────────────────────


async def stream_run_listing_loop_tokens(
    *,
    candidate: ProductCandidate | dict,
    market_code: str,
    brand: str,
    competitor_brands: list[str],
    session_id: str,
    run_id: str,
    agents_models: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """listing run with per-token streaming + thinking sink. Same
    shape as ``stream_run_listing_loop``; the only difference is the
    driver uses ``agent.astream(stream_mode="messages")`` and a
    ThinkStreamFilter to split safe text (yielded as ``listing_chunk``)
    from think-block content (yielded as ``thinking``).
    """
    from langchain_core.messages import HumanMessage

    model = agents_models.get("listing")
    if model is None:
        raise StartupConfigError(["listing model"])

    from langchain_core.language_models.chat_models import BaseChatModel as _BCM
    from ..llm import AimuxChatModel
    from ..llm_langchain import AimuxLangchainAdapter

    adapter_model = (
        model
        if isinstance(model, _BCM) and not isinstance(model, AimuxChatModel)
        else AimuxLangchainAdapter(model)
    )
    candidate_payload = _candidate_payload(candidate)
    market_block = (
        "市场约束(US): 标题≤75字符; 五点5条(每条10-255字符,结尾无标点); "
        "描述≤2000字符; 搜索词≤250字节"
    )
    user_payload = (
        "候选商品数据:\n"
        f"- 品名(英): {candidate_payload.get('name_en', '')}\n"
        f"- 品名(中): {candidate_payload.get('name_cn', '')}\n"
        f"- 品类: {candidate_payload.get('category', '')}\n"
        f"- 货源价(CNY): {candidate_payload.get('source_price_cny', '')}\n"
        f"- 目标价(USD): {candidate_payload.get('target_price_usd', '')}\n"
        f"- 竞品品牌: {','.join(competitor_brands or []) or '(无)'}\n"
        f"- 自有品牌: {brand or '(无)'}\n"
        f"- 市场: {market_code}\n"
        f"- 约束: {market_block}"
    )
    from ..prompts import load_prompt
    _, draft_body = load_prompt("draft", subdir="listing")
    system_prompt = (
        f"{draft_body}\n\n"
        "## Agent 工作方式\n"
        "1. 用 submit_draft 提交完整草稿 JSON（item_name/bullet_point[5]"
        "/product_description/generic_keyword/product_type——product_type 用"
        "亚马逊产品类型关键词，大写下划线式，如 PET_FEEDER）。\n"
        "2. 收到 hard_failed=true 时按 issues 修改后再提交（最多 3 次）。\n"
        "3. 校验通过（hard_failed=false）后，输出一行最终确认："
        "`DRAFT_OK`，不要再提交。\n"
        "4. 候选数据在市场上下文里；搜索词禁止出现竞品品牌。\n"
        "5. product_type 必填——首次提交若漏写会被 hard_fail 报"
        "product_type_missing，按惯例从商品品类推断一个最贴近的亚马逊产品"
        "类型关键词（大写下划线式），不要写 human-readable 名。"
    )

    events_queue: asyncio.Queue = asyncio.Queue()
    thinking_mw = EventsMiddleware(events_queue.put, emit_text_deltas=False)

    from langchain.agents import create_agent
    agent = create_agent(
        model=adapter_model,
        tools=build_listing_tools(),
        system_prompt=system_prompt,
        middleware=[thinking_mw],
    )

    current_turn = {"i": 0}

    async def _thinking_sink(_turn_idx: int, text: str) -> None:
        if not text:
            return
        await events_queue.put(
            {"event": "thinking", "turn": _turn_idx, "delta": text}
        )

    thinking_mw.set_thinking_sink(_thinking_sink)
    safe_filter = ThinkStreamFilter(sink=None)

    messages = [HumanMessage(content=user_payload)]

    async def _drive_chunks():
        try:
            async for chunk, _meta in agent.astream(
                {"messages": messages}, stream_mode="messages"
            ):
                content = getattr(chunk, "content", "")
                if isinstance(content, list):
                    text = "".join(
                        item.get("text", "")
                        for item in content
                        if isinstance(item, dict)
                    )
                else:
                    text = content if isinstance(content, str) else ""
                if text:
                    safe = safe_filter.feed(text)
                    if safe:
                        yield {
                            "event": "listing_chunk",
                            "delta": safe,
                            "turn": current_turn["i"],
                        }
        finally:
            leftover = safe_filter.flush()
            if leftover:
                yield {
                    "event": "listing_chunk",
                    "delta": leftover,
                    "turn": current_turn["i"],
                }

    chunk_iter = _drive_chunks()
    try:
        while True:
            try:
                ev = events_queue.get_nowait()
            except asyncio.QueueEmpty:
                try:
                    ev = await asyncio.wait_for(chunk_iter.__anext__(), timeout=0.05)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    if events_queue.empty():
                        break
                    continue
            if ev.get("event") == "turn_start":
                current_turn["i"] = ev.get("turn", current_turn["i"] + 1)
                safe_filter._suppressing = False
                safe_filter._buf = ""
            yield ev
    finally:
        pass
