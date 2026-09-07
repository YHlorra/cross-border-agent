"""Agent tool registry ( 1.3) — @tool wrappers over the existing deterministic
fixture/calc functions + the structured scoring step.

Design:
- 包装不改写：`data_tools` / `calc_tools` 已有全部确定性函数；这里只加
  LangChain ``@tool`` 描述层，工具闭包注入（不进 LangChain config）。
- `score_candidates` 是唯一调 LLM 的工具：内部保留 AimuxChatModel.structured
  + sanitize_scores 封顶（结构化输出 + 纯规则红线不动）。
  该工具在 create_agent 的 ToolNode 里被调用——结构化调用走 sync 线程池
  （沿用评分管线的同一并发安全面）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from langchain_core.tools import tool

from ..llm import AimuxChatModel
from ..state import (
    DimensionScore,
    ProductCandidate,
    ScoredCandidate,
)
from ..tools.adapters.local_json import LocalJSONAdapter
from ..tools.adapters.pg_corpus import PgCorpusAdapter


def _default_adapter() -> LocalJSONAdapter:
    """生产默认:亚马逊侧走 PG 语料(28k 真实商品),1688/趋势/属性走 fixture。
    显式传 adapter 的调用方(测试/工具)不受影响。"""
    return PgCorpusAdapter()
from ..tools.calc_tools import PRICE_GAP_RATIO_THRESHOLD, calculate_profit, match_price_gap
from ..tools.data_tools import (
    search_1688_products,
    search_amazon_competitors,
    get_category_trend,
)
from .memory_graph import render_traverse_paths, traverse_edges

try:  # pydantic schema availability (always installed with langchain-core)
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover - defensive
    BaseModel = Field = None  # type: ignore[assignment,misc]

# Number of candidates scored in one score_candidates call (each is one LLM
# call; the budget middleware caps total tool calls per run).
MAX_SCORE_PER_CALL = 3

# Hard cap on candidates in one submitted report ('s ≤12 carries over).
MAX_REPORT_CANDIDATES = 12

_DECISIONS = ("go", "caution", "no-go")

# Weights from quality_score.py — must sum to 1.0.
WEIGHTS: dict[str, float] = {
    "market_demand": 0.25,
    "competition": 0.20,
    "profit": 0.30,
    "seasonality": 0.10,
    "repurchase": 0.15,
}


class _ScoredOutput(BaseModel):
    """LLM structured-output shape per candidate (mirror of
    QualityScoreOutput in quality_score.py, minus pydantic nesting)."""

    product_id: str
    market_demand: dict
    competition: dict
    profit: dict
    seasonality: dict
    repurchase: dict
    opportunities: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class ScoreBatch(BaseModel):
    """One tool call scores up to MAX_SCORE_PER_CALL candidates in parallel."""

    candidates: list[_ScoredOutput] = Field(default_factory=list)


def validate_report_payload(
    candidates: list[dict], decision: str
) -> tuple[list[ScoredCandidate] | None, str | None]:
    """Structured final-envelope truth source ( 2.1).

    Legal: 1..12 candidates each passing ``ScoredCandidate.model_validate``,
    decision ∈ {go, caution, no-go} and equal to the top-scored candidate's
    recommendation. Returns (candidates, None) when legal, (None, reason)
    otherwise — the caller feeds the reason back as the tool's error JSON
    (an observation for the model, never a silent fallback).
    """
    if not isinstance(candidates, list) or not (1 <= len(candidates) <= MAX_REPORT_CANDIDATES):
        return None, (
            f"candidates must be a JSON array of 1..{MAX_REPORT_CANDIDATES} scored candidates"
        )
    scored: list[ScoredCandidate] = []
    for i, row in enumerate(candidates):
        try:
            scored.append(ScoredCandidate.model_validate(row))
        except Exception as e:  # noqa: BLE001 — surface shape problems honestly
            return None, f"candidates[{i}] failed ScoredCandidate validation: {e}"
    if decision not in _DECISIONS:
        return None, f"decision must be one of {list(_DECISIONS)}, got: {decision!r}"
    top = max(scored, key=lambda c: c.total_score or 0)
    if top.recommendation != decision:
        return None, (
            f"decision {decision!r} does not match top candidate "
            f"{top.name_cn or top.product_id} recommendation {top.recommendation!r}"
        )
    return scored, None


def build_search_tools(data: Any) -> list[Any]:
    """Marketplace search tools shared by the selection and general-agent
    registries (general-agent-chat D3) — same wrappers, one truth source."""

    @tool
    def search_1688(keyword: str, min_price_cny: Optional[float] = None, max_price_cny: Optional[float] = None) -> str:
        """检索 1688 货源。keyword: **中文**品类词（如 空气炸锅 / 宠物喂食器）；
        min/max_price_cny: 采购价区间（人民币）。当前 1688 无真源数据——
        如实返回空数组，不要因此放弃选品（亚马逊侧数据完整可用）。
        返回 JSON 数组（product_id/name_cn/price_cny/category/…）。"""
        rows = search_1688_products(
            data, keyword, min_price=min_price_cny, max_price=max_price_cny
        )
        return json.dumps(rows, ensure_ascii=False)

    @tool
    def search_amazon(keyword: str) -> str:
        """检索亚马逊竞品语料（28k 真实商品）。keyword: **英文**品类词
        （如 air fryer / pet feeder / rice cooker）——语料是英文搜索页抓取，
        中文词查不到。返回 JSON 数组（asin/price_usd/rating/review_count/
        image_url/category/…）。"""
        rows = search_amazon_competitors(data, keyword)
        return json.dumps(rows, ensure_ascii=False)

    @tool
    def category_trend(category: str) -> str:
        """品类级趋势查询（fixture 数据）。category: 英文类目标识（如
        pet_supplies / electronics_audio）。返回 JSON 或空串。"""
        row = get_category_trend(data, category)
        return json.dumps(row, ensure_ascii=False) if row else ""

    @tool
    def corpus_overview(keyword: str = "") -> str:
        """语料覆盖度探测（**选品请求第一步**）：查语料库有没有某品类的真实
        商品、多少款、什么价格带。keyword: **英文**品类词（如 air fryer /
        pet feeder——语料是英文亚马逊数据，中文词查不到）；留空返回全部
        大类×数量概况。有数据再引导用户细化筛选条件，没数据直接如实告知
        该品类暂无数据，不要带用户空转筛选。"""
        from ..tools.adapters.pg_corpus import corpus_overview

        overview = corpus_overview(keyword or None)
        return json.dumps(overview, ensure_ascii=False)

    return [search_1688, search_amazon, category_trend, corpus_overview]


def build_skill_tools() -> list[Any]:
    """领域 skill 渐进披露工具：L1 空参返回技能
    目录（name + description），L2 按名取全文。skill 文件放
    ``prompts/skills/*.md`` 即自动可发现，零代码注册。"""

    @tool
    def load_skill(name: str = "") -> str:
        """按名加载领域技能全文（打法手册/SOP）；name 留空返回可用技能目录
        （name + description 清单）。需要领域方法论（生图六要素、Listing 文案
        公式、选品打法等）时：先留空调目录，再按名取全文并按其中框架执行。
        已知技能：ecom-selection-method（选品方法论）/ ecom-imagegen（商品
        生图 SOP）/ ecom-listing-copywriting（Listing 文案方法论）。"""
        from ..prompts import list_skills, load_skill as _load_skill

        if not name.strip():
            return json.dumps(
                {"skills": list_skills()}, ensure_ascii=False
            )
        body = _load_skill(name.strip())
        if body is None:
            return json.dumps(
                {
                    "error": f"unknown skill: {name!r}",
                    "available": [s["name"] for s in list_skills()],
                    "hint": "先用空参调用 load_skill 查看可用技能目录。",
                },
                ensure_ascii=False,
            )
        return body

    return [load_skill]


def build_agent_tools(
    *,
    adapter: Any = None,
    model: AimuxChatModel | None = None,
    db_path: Any = None,
) -> list[Any]:
    """Build the tool registry. ``adapter`` defaults to LocalJSONAdapter;
    ``model`` is the AimuxChatModel used by the structured scoring tool
    (injected per-run — not via LangChain config); ``db_path`` scopes the
    memory-graph traverse tool (None → the store's default runtime DB —
    the same one the server throat materializes edges into).
    """
    data = adapter or _default_adapter()

    @tool
    def match_candidates(keyword: str, min_price_cny: Optional[float] = None, max_price_cny: Optional[float] = None) -> str:
        """检索 1688 货源并自动按类目匹配亚马逊竞品，返回候选行 JSON（含
        product_id/name_cn/name_en/source_price_cny/target_price_usd/
        price_gap_ratio/category 与竞品快照字段）。候选行已剔除价差不足的货源。
        keyword: 中文品类词；min/max_price_cny: 采购价区间。"""
        raw_1688 = search_1688_products(
            data, keyword, min_price=min_price_cny, max_price=max_price_cny
        )
        raw_amz = search_amazon_competitors(data, keyword)
        matched = match_price_gap(raw_1688, raw_amz)
        # 类目月度搜索序列（category_trends fixture）随候选行下发 → 前端迷你图
        trend_cache: dict[str, list] = {}
        for row in matched:
            cat = row.get("category")
            if not cat:
                continue
            if cat not in trend_cache:
                trend = get_category_trend(data, cat)
                trend_cache[cat] = list(trend.get("monthly_searches") or []) if trend else []
            row["trend_series"] = trend_cache[cat]
        # 返回行含 _competitor 快照 → 后续 score/报告直接用
        if not matched:
            # 空结果给语义（而非裸 []）：LLM 不再误判工具损坏而自造候选
            # （真实 run 实测：8 次重试后编造 0 价候选）。
            return json.dumps(
                {
                    "candidates": [],
                    "note": (
                        f"未匹配到价差比 ≥{PRICE_GAP_RATIO_THRESHOLD} "
                        f"的货源-竞品对（匹配 {len(raw_1688)} 货源 × {len(raw_amz)} 竞品）。"
                        "可换关键词/放宽价格区间，或如实报告该品类无可推荐候选。"
                    ),
                },
                ensure_ascii=False,
            )
        return json.dumps(matched, ensure_ascii=False)

    @tool
    def hot_filter_candidates(candidates_json: str) -> str:
        """规则热度初筛（纯规则，不调 LLM）：BSR Top≤30% 或月搜索量≥1000、
        90 天增长≥-10%。输入 match_candidates 的候选 JSON；返回 JSON
        {passed: [...], reasons: {product_id: 通过/淘汰原因}}。"""
        try:
            rows = json.loads(candidates_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"invalid candidates_json: {e}"}, ensure_ascii=False)
        from ..nodes.hot_filter import _passes_rules

        passed: list[dict] = []
        reasons: dict[str, str] = {}
        for row in rows:
            cand = ProductCandidate(
                product_id=row["product_id"],
                name_cn=row.get("name_cn", ""),
                name_en=row.get("name_en", row.get("name_cn", "")),
                source_price_cny=float(row.get("source_price_cny", 0)),
                target_price_usd=float(row.get("target_price_usd", 0)),
                price_gap_ratio=float(row.get("price_gap_ratio", 0)),
                category=row.get("category", ""),
                bsr_rank=row.get("bsr_rank"),
                bsr_top_percent=row.get("bsr_top_percent"),
                review_count=int(row.get("review_count", 0)),
                monthly_search=row.get("monthly_search"),
                trend_growth=row.get("trend_growth"),
            )
            ok, reason = _passes_rules(cand)
            reasons[cand.product_id] = reason
            if ok:
                passed.append(row)
        return json.dumps({"passed": passed, "reasons": reasons}, ensure_ascii=False)

    @tool
    def calculate_profit_tool(source_price_cny: float, target_price_usd: float, weight_g: int) -> str:
        """确定性利润测算（纯函数，LLM 不参与数字）。输入采购价(¥)/售价($)/
        重量(g)；返回 JSON：landed_cost/freight/commission/fba/net_profit/
        profit_margin。"""
        b = calculate_profit(
            source_price_cny=source_price_cny,
            target_price_usd=target_price_usd,
            weight_g=weight_g,
        )
        return b.model_dump_json()

    @tool
    def score_candidates(candidates_json: str, seed_keyword: str = "") -> str:
        """对候选商品做五维评分（市场需求/竞争度/利润/季节性/复购，权重加权）并
        返回 JSON 数组：每候选含 total_score/recommendation(go|caution|no-go)/
        opportunities/risks + 原候选字段。candidates_json: match_candidates 或
        hot_filter_candidates 的候选数组 JSON；一次最多评 MAX_SCORE_PER_CALL 个
        ——候选多时先评最重要的，剩余留给下一轮调用。"""
        if model is None:
            return json.dumps({"error": "scoring model not wired"}, ensure_ascii=False)
        try:
            rows = json.loads(candidates_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"invalid candidates_json: {e}"}, ensure_ascii=False)
        if not isinstance(rows, list):
            rows = (rows or {}).get("passed", []) if isinstance(rows, dict) else []
        # 兜底：LLM 可能绕过 match_candidates 直接喂 raw
        # 1688 行（无 target_price_usd/_competitor）——自动补语料竞品匹配，
        # 否则市场信号全空（真实 run 实测复现：耳机 3 候选全 0 价 0 评论）。
        if rows and any(
            "target_price_usd" not in r or not isinstance(r.get("_competitor"), dict)
            for r in rows
        ):
            raw = [
                {**r, "price_cny": r.get("price_cny", r.get("source_price_cny", 0))}
                for r in rows
            ]
            seed = seed_keyword or next((r.get("category", "") for r in raw), "")
            rows = match_price_gap(raw, data.search_amazon(seed))
            # 与 match_candidates 同源：类目月度搜索序列随行下发（前端迷你图）
            _trend_cache: dict[str, list] = {}
            for row in rows:
                cat = row.get("category")
                if not cat:
                    continue
                if cat not in _trend_cache:
                    trend = get_category_trend(data, cat)
                    _trend_cache[cat] = (
                        list(trend.get("monthly_searches") or []) if trend else []
                    )
                row["trend_series"] = _trend_cache[cat]
        top = rows[:MAX_SCORE_PER_CALL]

        cands = [
            ProductCandidate(
                product_id=r["product_id"],
                name_cn=r.get("name_cn", ""),
                name_en=r.get("name_en", r.get("name_cn", "")),
                source_price_cny=float(r.get("source_price_cny", 0)),
                target_price_usd=float(r.get("target_price_usd", 0)),
                price_gap_ratio=float(r.get("price_gap_ratio", 0)),
                category=r.get("category", ""),
                bsr_rank=r.get("bsr_rank"),
                bsr_top_percent=r.get("bsr_top_percent"),
                review_count=int(r.get("review_count", 0)),
                monthly_search=r.get("monthly_search"),
                trend_growth=r.get("trend_growth"),
                trend_series=r.get("trend_series") or [],
                weight_g=r.get("weight_g"),
                image_url=r.get("image_url"),
            )
            for r in top
        ]

        scored: list[ScoredCandidate] = _score_batch_sync(model, cands, seed_keyword)
        return json.dumps([s.model_dump() for s in scored], ensure_ascii=False)

    @tool
    def submit_report(candidates_json: str, decision: str, seed_keyword: str = "") -> str:
        """提交本次选品的结构化最终结果（输出最终 Markdown 报告之前必须调用）。
        candidates_json: 已评分候选数组 JSON（score_candidates 返回的数组原样或
        排序子集，1..12 条）；decision: 总体决策 go|caution|no-go，必须等于总分
        最高候选的 recommendation；seed_keyword: 用户种子关键词。"""
        try:
            rows = json.loads(candidates_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"invalid candidates_json: {e}"}, ensure_ascii=False)
        if not isinstance(rows, list):
            rows = []
        scored, err = validate_report_payload(rows, str(decision))
        if err is not None:
            return json.dumps({"error": err}, ensure_ascii=False)
        return json.dumps(
            {
                "ok": True,
                "count": len(scored),
                "decision": decision,
                "seed_keyword": seed_keyword,
            },
            ensure_ascii=False,
        )

    @tool
    def graph_traverse(entity_type: str, entity_id: str, rel: Optional[str] = None, hops: int = 1) -> str:
        """跨 run 历史记忆查询——仅当问题涉及历史对比或因果链时使用（如
        「上次推荐的为什么这次没上榜」「这个品类上次的结论是什么」）；常规
        选品不需要调用。entity_type: product|run|amazon|category|listing；
        entity_id: 对应稳定 id（product_id / run_id / asin / 品类名）；
        rel 可选（supersedes / decided / based_on / in_category …）；
        hops: 1 或 2。返回类型化路径文本，无相关记忆时明确说明。"""
        try:
            rows = traverse_edges(
                entity_type=entity_type,
                entity_id=entity_id,
                rel=rel,
                hops=hops,
                db_path=db_path,
            )
        except ValueError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
        return render_traverse_paths(rows)

    # Shared search wrappers (general-agent-chat D3) unpacked in the
    # historical registry order — create_agent binds by name, but the list
    # shape is asserted by tests and any positional consumer stays stable.
    # corpus_overview 追加尾部（新工具不挤占既有索引）。
    (
        search_1688,
        search_amazon,
        category_trend,
        corpus_overview_tool,
    ) = build_search_tools(data)
    return [
        search_1688,
        search_amazon,
        match_candidates,
        hot_filter_candidates,
        category_trend,
        calculate_profit_tool,
        score_candidates,
        submit_report,
        graph_traverse,
        corpus_overview_tool,
    ]


def _score_batch_sync(model: AimuxChatModel, cands: list[ProductCandidate], keyword: str) -> list[ScoredCandidate]:
    """Score a batch of candidates via the structured model (thread-safe sync).

    Batch size is capped by MAX_SCORE_PER_CALL (≤3) at the score_candidates
    tool; real profit figures are fed to the LLM and sanitize_scores keeps
    the 1-10 clamp and the data-insufficient cap
    quality_score.py contract).
    """
    from ..nodes.quality_score import sanitize_scores, _recommendation, _weighted_total, QualityScoreOutput
    from ..prompts import load_prompt

    _, system_prompt = load_prompt("quality_score")

    async def _score_one(c: ProductCandidate) -> ScoredCandidate:
        profit = calculate_profit(
            source_price_cny=c.source_price_cny,
            target_price_usd=c.target_price_usd,
            weight_g=c.weight_g or 250,
        )
        user_payload = (
            f"种子关键词: {keyword}\n"
            f"商品: {c.name_cn} / {c.name_en}\n"
            f"类目: {c.category}\n"
            f"采购价: ¥{c.source_price_cny} ({profit.landed_cost_usd} USD)\n"
            f"售价: ${c.target_price_usd}\n"
            f"重量: {c.weight_g}g\n"
            f"头程: ${profit.freight_usd}; 佣金: ${profit.commission_usd}; "
            f"FBA: ${profit.fba_fee_usd}\n"
            f"净利润: ${profit.net_profit_usd}; 利润率: {profit.profit_margin:.0%}\n"
            f"BSR: {c.bsr_rank} (Top {c.bsr_top_percent}%)\n"
            f"竞品评论: {c.review_count}\n"
            f"月搜索量: {c.monthly_search}; 90天增长: {c.trend_growth}"
        )
        out = await model.structured(
            system=system_prompt,
            user=user_payload,
            schema=_ScoredOutput,
            temperature=0.3,
        )
        # Reuse the exact sanitize_scores contract: rebuild a QualityScoreOutput
        # (it reads DimensionScore attrs per dimension) — no parallel scoring
        # implementation.
        q = QualityScoreOutput(
            product_id=out.product_id,
            market_demand=DimensionScore(**out.market_demand),
            competition=DimensionScore(**out.competition),
            profit=DimensionScore(**out.profit),
            seasonality=DimensionScore(**out.seasonality),
            repurchase=DimensionScore(**out.repurchase),
            opportunities=out.opportunities,
            risks=out.risks,
        )
        scores = sanitize_scores(q, c)
        total = _weighted_total(scores)
        return ScoredCandidate(
            **c.model_dump,
            scores=scores,
            total_score=total,
            recommendation=_recommendation(total),
            opportunities=q.opportunities,
            risks=q.risks,
        )

    async def _run_all() -> list[ScoredCandidate]:
        return list(await asyncio.gather(*(_score_one(c) for c in cands)))

    return asyncio.run(_run_all())
