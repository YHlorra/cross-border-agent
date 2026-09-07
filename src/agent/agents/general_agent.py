"""General store-manager agent (general-agent-chat) — the default chat loop.

Codex paradigm: plain input goes to ONE general agent; the domain pipelines
(selection / listing) are TOOLS in its registry, invoked on intent. The tool
closures drive the existing nested loops (``stream_run_loop`` /
``stream_run_listing_loop``) end-to-end, persist through the same throat
contract (selection_runs / listing_runs + memory-graph edges), and surface the
result to the user as a ``card`` wire event — the model only sees a compact
summary so it summarizes instead of re-narrating the report.

Wire vocabulary adds exactly one event: ``card``. Everything else
(turn_start / agent_delta / tool_call / tool_result / final / error) keeps the
established semantics; the chat ``final`` carries ``kind:"chat"`` plus the
assistant text.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import aclosing
from typing import Any, AsyncIterator, Optional

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool as lc_tool

from ..llm import AimuxChatModel, StartupConfigError
from ..prompts import load_prompt, slash_skill_appendix
from .tools import _default_adapter
from .events_middleware import EventsMiddleware
from .listing_agent import stream_run_listing_loop
from .loop_runtime import stream_run_loop
from .memory_graph import materialize_edges_for_run
from .tools import build_search_tools, build_skill_tools

log = logging.getLogger(__name__)

_BUDGET_WARNING = (
    "预算将尽（本轮已达工具调用上限）：立即基于现有信息用文字回复用户，不要再调用任何工具。"
)


def build_general_agent(
    *,
    model: BaseChatModel,
    tools: list[Any],
    events_queue: Optional[asyncio.Queue] = None,
    history_context: str = "",
    middleware: Optional[list[AgentMiddleware]] = None,
    system_prompt: Optional[str] = None,
    name: str = "general_agent",
) -> Any:
    """Compile the general agent — same create_agent wiring as the selection
    agent, but with the chat prompt and a conversation-flavored budget
    warning. ``history_context`` is the prose chat transcript (D5)."""
    if system_prompt is None:
        _, body = load_prompt("chat", subdir="general")
        system_prompt = body

    if history_context and history_context.strip():
        system_prompt = (
            f"{system_prompt}\n\n## 会话历史（近几轮对话，指代消解用）\n{history_context.strip()}"
        )

    mw: list[AgentMiddleware] = list(middleware or [])
    if events_queue is not None:
        mw.append(EventsMiddleware(events_queue.put, budget_warning=_BUDGET_WARNING))

    return create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=mw,
        name=name,
    )


# ─── persistence hooks  ──────────────────────────────────────────────────


def default_persist_hooks() -> Any:
    """Production hooks — the store functions behind the server throat.
    Tests inject recorders instead (no SQLite)."""
    from ..persistence import save_listing_run, save_run

    class _Hooks:
        def selection_run(self, *, query: str, final: dict, run_id: str, session_id: str) -> None:
            report = final.get("report") if isinstance(final.get("report"), dict) else {}
            candidates = final.get("candidates") or []
            envelope = {"report": report, "candidates": candidates, "intent": None}
            seed_keyword = str(report.get("seed_keyword") or "")
            save_run(
                query=query,
                seed_keyword=seed_keyword,
                decision=str(final.get("decision") or ""),
                report=envelope,
                run_id=run_id,
            )
            materialize_edges_for_run(
                run_id=run_id,
                session_id=session_id,
                keyword=seed_keyword,
                decision=str(final.get("decision") or ""),
                report=envelope,
                candidates=candidates,
            )

        def listing_run(self, *, candidate: dict, final: dict, run_id: str, session_id: str) -> None:
            save_listing_run(
                session_id=session_id,
                product_id=str(final.get("product_id") or ""),
                market_code=str(final.get("market_code") or "US"),
                listing={
                    "listing": final.get("listing"),
                    "issues": final.get("issues") or [],
                    "hard_failed": bool(final.get("hard_failed")),
                    "candidate": candidate,
                },
                run_id=run_id,
            )

    return _Hooks()


# ─── domain tools  ───────────────────────────────────────────────────────


def build_domain_tools(
    *,
    emit: Any,
    session_id: str,
    agents_models: dict[str, Any],
    persist: Any | None = None,
) -> list[Any]:
    """run_selection / run_listing — the nested-pipeline tools. ``emit``
    receives wire events (the card channel); ``persist`` defaults to the
    store-backed hooks."""
    hooks = persist if persist is not None else default_persist_hooks()

    @lc_tool
    async def run_selection(query: str) -> str:
        """执行一次完整选品流水线（检索→匹配→热度筛选→五维评分→报告），结果卡片
        会直接展示给用户。query: 用户的选品需求描述（品类 + 预算 + 偏好，尽量
        原样传递用户原话）。返回 JSON 摘要（decision/top 候选/run_id）——你只需
        用一两句话点评或追问，不要复述完整报告。"""
        nested_run_id = str(uuid.uuid4())
        final: dict | None = None
        empty_event: dict | None = None
        stream = stream_run_loop(
            query=query,
            session_id=session_id,
            run_id=nested_run_id,
            history_context="",
            agents_models=agents_models,
        )
        try:
            async with aclosing(stream) as it:
                async for ev in it:
                    if ev.get("event") == "final":
                        final = ev
                    elif ev.get("event") == "empty":
                        empty_event = ev
        except Exception as e:  # noqa: BLE001 — 上游 LLM/API 故障转结构化：
            # 异常直接冒泡会让聊天台只见哑的「执行失败」；转成可转述的 JSON，
            # general agent 能向用户解释原因并建议下一步。
            log.exception("nested selection run failed")
            return json.dumps(
                {
                    "error": f"selection pipeline failed: {e}",
                    "hint": "本轮选品执行失败（上游模型/网络故障）。请向用户说明失败，"
                    "并建议稍后重试或调整需求描述后再跑一轮。",
                },
                ensure_ascii=False,
            )

        if final is None:
            return json.dumps(
                {"error": "selection pipeline produced no final envelope"},
                ensure_ascii=False,
            )

        candidates = final.get("candidates") or []
        try:
            hooks.selection_run(
                query=query, final=final, run_id=nested_run_id, session_id=session_id
            )
        except Exception:  # noqa: BLE001 — persistence is best-effort (throat contract)
            log.exception("nested selection persist failed (non-fatal)")

        if candidates:
            await emit(
                {
                    "event": "card",
                    "card": {
                        "type": "selection",
                        "run_id": nested_run_id,
                        "decision": final.get("decision") or "",
                        "report": final.get("report"),
                        "candidates": candidates,
                        "market_summary": final.get("market_summary") or "",
                    },
                }
            )
            tops = [
                {
                    "name_cn": c.get("name_cn", ""),
                    "total_score": c.get("total_score"),
                    "recommendation": c.get("recommendation", ""),
                }
                for c in candidates[:3]
            ]
            return json.dumps(
                {
                    "ok": True,
                    "run_id": nested_run_id,
                    "decision": final.get("decision") or "",
                    "count": len(candidates),
                    "top": tops,
                    "hint": "选品报告卡片已展示给用户；请用一两句话给出结论或追问，不要复述报告。",
                },
                ensure_ascii=False,
            )

        # honest empty result — no card; the model narrates why
        message = str((empty_event or {}).get("message") or "没有找到匹配的候选商品")
        return json.dumps(
            {
                "ok": True,
                "run_id": nested_run_id,
                "decision": final.get("decision") or "no-go",
                "count": 0,
                "message": message,
                "hint": "本次选品无候选结果，未生成卡片；请向用户解释原因并建议调整关键词或预算。",
            },
            ensure_ascii=False,
        )

    @lc_tool
    async def run_listing(candidate_json: str) -> str:
        """为候选商品生成 Amazon Listing 草稿（合规校验含自动修复），结果卡片会
        直接展示给用户。candidate_json: 候选商品 JSON——优先原样引用选品卡片里
        的候选对象（含 product_id/name_cn/name_en/target_price_usd/category），
        不要编造字段。返回 JSON 摘要（run_id/标题预览）。"""
        try:
            payload = json.loads(candidate_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"invalid candidate_json: {e}"}, ensure_ascii=False)
        if not isinstance(payload, dict):
            return json.dumps({"error": "candidate_json must be a JSON object"}, ensure_ascii=False)
        required = ("product_id", "name_cn", "name_en", "target_price_usd")
        missing = [k for k in required if k not in payload]
        if missing:
            return json.dumps(
                {"error": f"candidate missing: {missing}（应引用选品结果里的候选对象）"},
                ensure_ascii=False,
            )

        nested_run_id = str(uuid.uuid4())
        final: dict | None = None
        stream = stream_run_listing_loop(
            candidate=payload,
            market_code="US",
            brand="",
            competitor_brands=[],
            session_id=session_id,
            run_id=nested_run_id,
            agents_models=agents_models,
        )
        async with aclosing(stream) as it:
            async for ev in it:
                if ev.get("event") == "final" and ev.get("kind") == "listing":
                    final = ev

        if final is None:
            return json.dumps(
                {"error": "listing pipeline produced no validated draft"},
                ensure_ascii=False,
            )

        try:
            hooks.listing_run(
                candidate=payload, final=final, run_id=nested_run_id, session_id=session_id
            )
        except Exception:  # noqa: BLE001
            log.exception("nested listing persist failed (non-fatal)")

        listing = final.get("listing") or {}
        await emit(
            {
                "event": "card",
                "card": {
                    "type": "listing",
                    "run_id": nested_run_id,
                    "product_id": final.get("product_id") or "",
                    "candidate": payload,
                    "listing": listing,
                    "issues": final.get("issues") or [],
                    "hard_failed": bool(final.get("hard_failed")),
                },
            }
        )
        title = str(listing.get("item_name") or "")[:60]
        return json.dumps(
            {
                "ok": True,
                "run_id": nested_run_id,
                "title_preview": title,
                "hard_failed": bool(final.get("hard_failed")),
                "hint": "Listing 卡片已展示给用户；请用一两句话说明草稿要点（如标题方向），不要复述全文。",
            },
            ensure_ascii=False,
        )

    @lc_tool
    async def save_listing(listing_json: str, candidate_json: str = "") -> str:
        """把你（agent）按 Listing 文案方法论起草的 Listing 存入 Listing 列表：
        程序化格式校验（字符数/禁令/搜索词字节）→ 落库 → 展示草稿卡片。
        listing_json: 完整 Listing JSON（item_name/bullet_point[5]/
        product_description/generic_keyword，字段名与亚马逊 SP-API 一致）。
        candidate_json: 可选候选商品 JSON（含 product_id/name_cn/name_en/
        target_price_usd）——有选品结果时原样引用；缺省时从 Listing 标题合成
        最小候选。校验 hard_failed 时按返回的 issues 修正后重新保存。"""
        from ..listing.schema import ListingOutput, load_market_config
        from ..listing.validate import validate_listing

        try:
            payload = json.loads(listing_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"invalid listing_json: {e}"}, ensure_ascii=False)
        if not isinstance(payload, dict):
            return json.dumps({"error": "listing_json must be a JSON object"}, ensure_ascii=False)
        try:
            draft = ListingOutput(**payload)
        except Exception as e:  # pydantic ValidationError — 字段名/类型不符
            return json.dumps(
                {
                    "error": f"listing_json fields invalid: {e}",
                    "hint": "字段名与 SP-API 一致：item_name / bullet_point[5] / "
                    "product_description / generic_keyword（后三个可省略）。",
                },
                ensure_ascii=False,
            )

        candidate: dict = {}
        if candidate_json.strip():
            try:
                candidate = json.loads(candidate_json)
            except json.JSONDecodeError as e:
                return json.dumps({"error": f"invalid candidate_json: {e}"}, ensure_ascii=False)
            if not isinstance(candidate, dict):
                return json.dumps(
                    {"error": "candidate_json must be a JSON object"}, ensure_ascii=False
                )
        if not candidate:
            title = draft.item_name.strip()
            candidate = {
                "product_id": f"manual-{uuid.uuid4().hex[:8]}",
                "name_cn": title[:40],
                "name_en": title[:60],
                "target_price_usd": 0.0,
                "category": "",
            }

        result = validate_listing(draft, load_market_config())
        validated = result.output
        nested_run_id = str(uuid.uuid4())
        final = {
            "event": "final",
            "kind": "listing",
            "listing": validated.model_dump(),
            "issues": [i.model_dump() for i in result.issues],
            "hard_failed": result.hard_failed,
            "product_id": str(candidate.get("product_id") or ""),
            "market_code": "US",
        }
        try:
            hooks.listing_run(
                candidate=candidate, final=final, run_id=nested_run_id, session_id=session_id
            )
        except Exception:  # noqa: BLE001
            log.exception("save_listing persist failed (non-fatal)")

        await emit(
            {
                "event": "card",
                "card": {
                    "type": "listing",
                    "run_id": nested_run_id,
                    "product_id": final["product_id"],
                    "candidate": candidate,
                    "listing": final["listing"],
                    "issues": final["issues"],
                    "hard_failed": result.hard_failed,
                },
            }
        )
        return json.dumps(
            {
                "ok": True,
                "run_id": nested_run_id,
                "hard_failed": result.hard_failed,
                "issues": final["issues"],
                "hint": (
                    "Listing 已入列表并展示卡片；请用一两句话说明草稿要点，"
                    "不要复述全文。hard_failed=true 时按 issues 修正后重新调用本工具。"
                    if result.hard_failed
                    else "Listing 已入列表并展示卡片；请用一两句话说明草稿要点，不要复述全文。"
                ),
            },
            ensure_ascii=False,
        )

    return [run_selection, run_listing, save_listing]


# ─── driver ──────────────────────────────────────────────────────────────────


async def stream_general_loop(
    *,
    query: str,
    session_id: str,
    run_id: str,
    history_context: str,
    agents_models: dict[str, Any],
    persist: Any | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Drive one general-agent chat run; yields the shared wire vocabulary
    plus ``card`` events, ending with ``final {kind:"chat", text}``.
    Persistence of the chat run itself happens in the caller (server throat);
    nested selection/listing runs persist through the hooks."""
    model = agents_models.get("selection")
    if model is None:
        raise StartupConfigError(["selection model"])
    # Same adapter contract as stream_run_loop: production passes the
    # graph-runtime AimuxChatModel (wrap it), SEAM tests pass a BaseChatModel
    # fake (use as-is).
    from ..llm_langchain import AimuxLangchainAdapter

    adapter_model = (
        model
        if isinstance(model, BaseChatModel) and not isinstance(model, AimuxChatModel)
        else AimuxLangchainAdapter(model)
    )
    # Domain tools emit cards onto the same queue the middleware drains.
    events_queue: asyncio.Queue = asyncio.Queue()

    async def _emit(ev: dict) -> None:
        await events_queue.put(ev)

    tools = [
        # 语料适配器（PgCorpusAdapter）：search_amazon 走 PG 语料 28k 真实商品
        # + corpus_overview 覆盖度探测
        *build_search_tools(_default_adapter()),
        # 领域 skill 渐进披露：load_skill 按需取
        # prompts/skills/ 下的方法论全文
        *build_skill_tools(),
        *build_domain_tools(
            emit=_emit,
            session_id=session_id,
            agents_models=agents_models,
            persist=persist,
        ),
    ]
    # Codex 风格 slash（ Addendum）：/选品、/listing 不再硬路由，
    # 由装配层把对应领域 skill 全文注入系统提示词附录；普通文本无附录。
    appendix = slash_skill_appendix(query)
    system_prompt: str | None = None
    if appendix:
        _, chat_body = load_prompt("chat", subdir="general")
        system_prompt = f"{chat_body}\n\n{appendix}"

    agent = build_general_agent(
        model=adapter_model,
        tools=tools,
        events_queue=events_queue,
        history_context=history_context,
        system_prompt=system_prompt,
    )

    async def _drive() -> list[Any]:
        out = await agent.ainvoke({"messages": [HumanMessage(content=query)]})
        return out["messages"]

    task = asyncio.create_task(_drive())
    try:
        while True:
            try:
                ev = await asyncio.wait_for(events_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                if task.done():
                    break
                continue
            yield ev

        msgs = task.result()
        agent_text = ""
        for m in reversed(msgs):
            from langchain_core.messages import AIMessage

            if isinstance(m, AIMessage) and m.content:
                agent_text = m.content if isinstance(m.content, str) else ""
                break

        yield {
            "event": "final",
            "kind": "chat",
            "text": agent_text,
            "session_id": session_id,
            "run_id": run_id,
        }
    finally:
        # Same turn-boundary cancel contract as the selection/listing drivers:
        # a client disconnect must not leave the loop spending LLM tokens
        # behind a dead response. Cancelling the outer task also unwinds any
        # in-flight nested pipeline (the tool's aclosing closes its generator,
        # whose finally cancels the nested task).
        if not task.done():
            task.cancel()
            log.info("general run %s cancelled by client", run_id)
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
