"""pg_corpus 适配器 SEAM。

硬依赖本机 PG@5433 的语料三表（scripts/load_corpus.py 装载；测试库用
conftest 的 12 行英文种子）。验证生产换源后的关键词检索、字段映射与
语言契约：

**语言契约（selection-skill）**：corpus 是英文亚马逊数据——search_amazon /
corpus_overview 只做诚实的 ILIKE，调用方（agent）负责用英文关键词。
适配器不再内置中英映射表（_TOKEN_ROUTES 已删：LLM 原生会翻译，手工路由表
既重复又脆弱——漏登记即静默空，空气炸锅「数据底座缺失」事故根因）。
"""
from __future__ import annotations

import os

import pytest

from agent.tools.adapters.pg_corpus import PgCorpusAdapter

# SEAM 硬依赖：语料三表已由 scripts/load_corpus.py 装入开发库。
# 注意 conftest 会把 DATABASE_URL 置空串——必须用 or 回退而非 get 默认值。
_DEV_URL = os.environ.get("DATABASE_URL") or "postgresql://postgres@127.0.0.1:5433/crossborder"

# 英文关键词基线（种子库每词 ≥2 命中：北极星的语料检索宽度）
_ENGLISH_WORDS = [
    "headphones",  # EA01/02/03 标题
    "earbuds",     # EA01 标题 + category_query
    "bluetooth",   # EA01/EA04 标题
    "cat",         # PET2/PET3 标题
]


@pytest.fixture(scope="module")
def adapter() -> PgCorpusAdapter:
    return PgCorpusAdapter(database_url=_DEV_URL)


def test_english_keywords_hit_more_than_one(adapter: PgCorpusAdapter) -> None:
    for word in _ENGLISH_WORDS:
        rows = adapter.search_amazon(word)
        assert len(rows) > 1, f"{word} 仅命中 {len(rows)} 条（种子库应为真实宽度）"


def test_row_shape_carries_market_signals(adapter: PgCorpusAdapter) -> None:
    rows = adapter.search_amazon("headphones")
    assert rows, "headphones 应命中语料"
    row = rows[0]
    # 商品本体
    assert row["asin"] and row["title"]
    assert row["image_url"]  # 语料 img 供电 product-card 主图
    # 市场信号（ETL 保证 price 非空的行才返回）
    assert isinstance(row["price_usd"], float) and row["price_usd"] > 0
    assert row["review_count"] >= 0
    # 语料无 brand/BSR/trend —— 如实 None，不伪造
    assert row["brand"] is None
    assert row["bsr_rank"] is None
    assert row["trend_growth_90d"] is None
    # category = 语料自带 major_category（真实类目，无路由表桥接）
    assert row["category"] == "electronics"


def test_language_contract_chinese_honest_empty_english_hits(adapter: PgCorpusAdapter) -> None:
    """selection-skill 语言契约：适配器不做翻译。中文词查英文语料 → 诚实空；
    英文词直接命中。语言选择是调用方（agent/skill）的职责——
    prompts/selection/agent.md 平台-语言策略表约定 Amazon 侧用英文。"""
    assert adapter.search_amazon("空气炸锅") == []  # 中文不命中英文语料，诚实空
    assert adapter.search_amazon("耳机") == []
    assert adapter.search_amazon("headphones"), "英文词直接命中"


def test_empty_keyword_returns_empty(adapter: PgCorpusAdapter) -> None:
    assert adapter.search_amazon("") == []
    assert adapter.search_amazon("   ") == []


def test_trend_and_1688_are_honest_empty(adapter: PgCorpusAdapter) -> None:
    """selection-cleanup 2026-09-06: 1688 / trend / attribute fixtures were
    deleted. PgCorpusAdapter inherits from LocalJSONAdapter (now a no-op
    stub). Until a real 1688 source is wired, these
    methods return []/None — not an error, but an honest-empty signal so
    the agent can report the gap rather than fabricate candidates."""
    assert adapter.get_category_trend("pet_supplies") is None
    assert adapter.search_1688("宠物用品") == []
    assert adapter.get_category_attributes("pet_supplies") is None


def test_corpus_overview_probes_coverage() -> None:
    """分段筛选第一步——覆盖度探测（与 search 同一 ILIKE 契约、同词表）。"""
    from agent.tools.adapters.pg_corpus import corpus_overview

    full = corpus_overview(None, database_url=_DEV_URL)
    assert full["total"] == 12 and len(full["by_major"]) == 3
    hit = corpus_overview("headphones", database_url=_DEV_URL)
    assert hit["matches"] and hit["matches"][0]["count"] >= 1
    assert hit["matches"][0]["price_min"] and hit["matches"][0]["price_max"]
    # 中文词诚实空（无翻译魔法）——探测与搜索同词表，同错同空
    miss = corpus_overview("耳机", database_url=_DEV_URL)
    assert miss["matches"] == []


def test_registry_includes_corpus_overview() -> None:
    from agent.agents.tools import build_agent_tools

    tools = build_agent_tools()
    assert tools[-1].name == "corpus_overview"
