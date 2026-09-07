"""PG 语料适配器。

亚马逊侧数据源 = corpus_products（亚马逊真实搜索页抓取，28,395 商品），
作为选品 mock 数据集（静态数据，无 live API）。

**语言契约（selection-skill 2026-09-06）**：语料是英文亚马逊数据——调用方
（agent）负责用英文关键词检索。此前这里有一张 `_TOKEN_ROUTES` 中英映射表
（空气炸锅→air fryer 等 ~40 条），在适配器里偷偷替 agent 翻译；实践证明
它是多余的重复（LLM 原生会翻译）且脆弱（每个新品类都要人工登记，漏登记
即静默空结果——「数据底座缺失」事故根因）。现在适配器只做诚实的 ILIKE
检索：给什么词查什么词，语言策略由 selection skill 在提示词层显式约定。

1688 货源 / 类目趋势 / 类目属性：无真源（JSON fixture 已删，见
selection-cleanup 82ef0fa），继承 LocalJSONAdapter no-op 桩返回空。
DATABASE_URL 在调用时读取（import 期读 env 是已登记的时序陷阱）。
"""
from __future__ import annotations

import os

import psycopg

from .local_json import LocalJSONAdapter

_SEARCH_LIMIT = 200

_SEARCH_SQL = (
    "SELECT asin, title, price_usd, rating, review_count, img, source_pos, "
    "major_category FROM corpus_products "
    "WHERE price_usd IS NOT NULL "
    "AND (title ILIKE %s OR category_query ILIKE %s)"
)

_OVERVIEW_MAJORS_SQL = (
    "SELECT major_category, count(*) AS n FROM corpus_products "
    "GROUP BY major_category ORDER BY n DESC"
)

_OVERVIEW_MATCH_SQL = (
    "SELECT category_query, count(*) AS n, "
    "min(price_usd) AS price_min, max(price_usd) AS price_max "
    "FROM corpus_products "
    "WHERE (title ILIKE %s OR category_query ILIKE %s) AND price_usd IS NOT NULL "
    "GROUP BY category_query ORDER BY n DESC LIMIT 12"
)


def row_to_amazon(row: tuple) -> dict:
    """语料行 → fixture amazon 行 shape（缺位字段如实 None）。"""
    asin, title, price_usd, rating, review_count, img, source_pos, major = row
    return {
        "asin": asin,
        "title": title,
        "brand": None,
        "price_usd": float(price_usd) if price_usd is not None else None,
        "rating": float(rating) if rating is not None else None,
        "review_count": int(review_count) if review_count is not None else 0,
        "image_url": img,
        "bsr_rank": None,
        "bsr_top_percent": None,
        "monthly_search_estimate": None,
        "trend_growth_90d": None,
        "monthly_sales_estimate": None,
        # 语料自带 major_category——配对/分组用真实类目，不再经路由表桥接
        "category": major,
    }


def _database_url(explicit: str | None) -> str | None:
    return explicit or os.environ.get("DATABASE_URL") or None


class PgCorpusAdapter(LocalJSONAdapter):
    """search_amazon 走 PG 语料（英文关键词，调用方负责语言）；其余三方法
    继承 LocalJSONAdapter no-op 桩（1688/趋势/属性无真源，诚实空）。"""

    def __init__(self, database_url: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._database_url = database_url  # None → 调用时读 env

    def _search_corpus(self, keyword: str) -> list[dict]:
        url = _database_url(self._database_url)
        if not url:
            return []
        pattern = f"%{keyword.strip()}%"
        sql = _SEARCH_SQL + " ORDER BY source_pos ASC NULLS LAST LIMIT %s"
        with psycopg.connect(url, connect_timeout=5) as conn:
            rows = conn.execute(sql, (pattern, pattern, _SEARCH_LIMIT)).fetchall()
        return [row_to_amazon(row) for row in rows]

    def search_amazon(self, keyword: str) -> list[dict]:
        """关键词原样 ILIKE title + category_query。**英文词进、英文数据出**——
        中文词不会命中英文语料（诚实空），语言选择是调用方（agent/skill）
        的职责，见 prompts/selection/agent.md 平台-语言策略。"""
        q = keyword.strip()
        if not q:
            return []
        return self._search_corpus(q)


def corpus_overview(keyword: str | None = None, database_url: str | None = None) -> dict:
    """语料覆盖度概况（分段筛选第一步）。

    keyword=None → 大类×数量全表；keyword 给定 → 命中类目数与价格区间
    （title/category_query ILIKE 原样检索——与 search_amazon 同一契约，
    探测与搜索永远同词表）。选品请求先调它确认「有货」再引导细化。
    """
    url = _database_url(database_url)
    if not url:
        return {"total": 0, "by_major": [], "matches": [], "note": "DATABASE_URL not set"}
    with psycopg.connect(url, connect_timeout=5) as conn:
        majors = [
            {"major": r[0], "count": r[1]}
            for r in conn.execute(_OVERVIEW_MAJORS_SQL).fetchall()
        ]
        merged: list[dict] = []
        if keyword and keyword.strip():
            pattern = f"%{keyword.strip()}%"
            for r in conn.execute(_OVERVIEW_MATCH_SQL, (pattern, pattern)).fetchall():
                merged.append(
                    {
                        "query": r[0],
                        "count": r[1],
                        "price_min": round(float(r[2]), 2) if r[2] is not None else None,
                        "price_max": round(float(r[3]), 2) if r[3] is not None else None,
                    }
                )
    total = sum(m["count"] for m in majors)
    return {"total": total, "by_major": majors, "matches": merged}