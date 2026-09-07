"""把 data/corpus/corpus.db（亚马逊真实抓取语料）整库装入 Postgres。

源库以只读 URI 打开（数据资产零写入）；目标三表
corpus_products / corpus_categories / corpus_meta 在 db/schema.ts 真相源。
幂等：重跑前 TRUNCATE 三表。完成后精确断言行数（出厂标签口径）：
products=28,395 / categories=1,000 / meta=10。

用法: .venv/Scripts/python.exe scripts/load_corpus.py
"""
from __future__ import annotations

import os
import sqlite3
import sys

import psycopg

CORPUS_DB = os.path.join(os.path.dirname(__file__), "..", "data", "corpus", "corpus.db")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/crossborder"
)

# 出厂标签实际 9 键（build_db.py 硬编码 9 条；初版计划误记 10，以源码为准）
EXPECTED = {"corpus_products": 28395, "corpus_categories": 1000, "corpus_meta": 9}

INSERTS = {
    "corpus_products": (
        "INSERT INTO corpus_products (asin,title,img,price_usd,rating,review_count,"
        "category_query,major_category,source_pos,scraped_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        "SELECT asin,title,img,price_usd,rating,review_count,category_query,"
        "major_category,source_pos,scraped_at FROM products",
        10,
    ),
    "corpus_categories": (
        "INSERT INTO corpus_categories (query,major,items_scraped,distinct_items,scraped_at) "
        "VALUES (%s,%s,%s,%s,%s)",
        "SELECT query,major,items_scraped,distinct_items,scraped_at FROM categories",
        5,
    ),
    "corpus_meta": (
        "INSERT INTO corpus_meta (key,value) VALUES (%s,%s)",
        "SELECT key,value FROM meta",
        2,
    ),
}


def main() -> None:
    src = sqlite3.connect(f"file:{os.path.abspath(CORPUS_DB)}?mode=ro", uri=True)
    dst = psycopg.connect(DATABASE_URL)
    try:
        with dst.cursor() as cur:
            cur.execute(
                "TRUNCATE corpus_products, corpus_categories, corpus_meta"
            )
            for table, (ins, sel, width) in INSERTS.items():
                rows = src.execute(sel).fetchall()
                for row in rows:
                    assert len(row) == width, f"{table} 列宽漂移: {len(row)} != {width}"
                cur.executemany(ins, rows)
                print(f"{table}: inserted {len(rows)}")
            # 精确行数断言（出厂标签口径）
            for table, expected in EXPECTED.items():
                got = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                assert got == expected, f"{table}: {got} != {expected}"
            # 抽样对照：一条商品完整字段 + meta 的 distinct_products 与 products 行数一致
            cur.execute(
                "SELECT asin,title,price_usd,rating,review_count,category_query,"
                "major_category,source_pos,scraped_at FROM corpus_products LIMIT 1"
            )
            sample = cur.fetchone()
            assert sample and sample[0] and sample[1], "抽样行字段为空"
            src_sample = src.execute(
                "SELECT asin,title,price_usd,rating,review_count,category_query,"
                "major_category,source_pos,scraped_at FROM products WHERE asin=?",
                (sample[0],),
            ).fetchone()
            assert src_sample == sample, f"抽样不一致:\nPG  {sample}\nsrc {src_sample}"
            declared = dict(
                cur.execute("SELECT key,value FROM corpus_meta").fetchall()
            )["distinct_products"]
            assert int(declared) == EXPECTED["corpus_products"], (
                f"meta.distinct_products({declared}) 与 products 行数不符"
            )
        dst.commit()
        print("ETL OK: 三表行数/抽样/meta 口径全部通过断言")
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    sys.exit(main())
