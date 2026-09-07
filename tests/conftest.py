"""Session-wide test env（PG-only 基座）。

测试硬依赖本机 PG@5433（scripts/pg_up.bat 启动）；DATABASE_URL 指向
crossborder_test（与运行库物理隔离，不存在则建库 + 应用 db/migrations）。
每个测试 TRUNCATE 应用表；corpus_* 语料种子只装一次不随测试清除。

密钥类变量置空串而非 pop——server 导入时 load_dotenv 会从 .env.local
回填缺失变量（不覆盖已有值），空串语义：live 测试 skip、Langfuse no-op、
durable off。PG 门控测试（test_pg_store.py）因 PG_TEST_DATABASE_URL
默认就位而转正进默认套件。
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

TEST_DB_URL = os.environ.get("PG_TEST_DATABASE_URL") or (
    "postgresql://postgres@127.0.0.1:5433/crossborder_test"
)
_ADMIN_URL = "postgresql://postgres@127.0.0.1:5433/postgres"
_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"

os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ.setdefault("PG_TEST_DATABASE_URL", TEST_DB_URL)
for _var in (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST",
    "AGENT_DURABLE",
    "AGENT_APPROVAL",
    "HUIWA_API_KEY",
):
    os.environ[_var] = ""

# 应用表（每测试清空）；corpus_* 是只读种子数据，不清
_APP_TABLES = (
    "selection_runs",
    "listing_runs",
    "messages",
    "entity_edges",
    "session_meta",
    "preference_embeddings",
    "approval_requests",
)

# 语料种子：三类目各 4 条真实 shape 行（Major 与 db/schema corpus_products 对齐），
# 足以驱动 match_price_gap 配对与 SEAM 断言；固定 ASIN 幂等。
_CORPUS_SEED = [
    # (asin, title, img, price, rating, reviews, query, major, pos)
    ("B0TESTEA01", "Wireless Earbuds Bluetooth 5.3 Headphones", "https://img.example/ea1.jpg", 19.99, 4.2, 320, "wireless earbuds", "electronics", 1),
    ("B0TESTEA02", "Bone Conduction Headphones Open Ear Sport", "https://img.example/ea2.jpg", 32.99, 4.5, 186, "running headphones", "electronics", 2),
    ("B0TESTEA03", "ANC Over Ear Headphones HiFi Stereo", "https://img.example/ea3.jpg", 45.5, 4.4, 512, "wireless earbuds", "electronics", 3),
    ("B0TESTEA04", "Bluetooth Speaker Portable Waterproof", "https://img.example/ea4.jpg", 24.0, 4.1, 95, "bluetooth speaker", "electronics", 4),
    ("B0TESTPET1", "Self Cleaning Slicker Brush for Dogs Cats", "https://img.example/p1.jpg", 15.99, 4.6, 2100, "dog brush", "pet_supplies", 1),
    ("B0TESTPET2", "Stainless Steel Cat Bowl Elevated", "https://img.example/p2.jpg", 12.5, 4.3, 430, "cat bowl", "pet_supplies", 2),
    ("B0TESTPET3", "Interactive Cat Toy Automatic", "https://img.example/p3.jpg", 21.0, 4.0, 88, "cat toy", "pet_supplies", 3),
    ("B0TESTPET4", "Aquarium Filter Quiet Fish Tank", "https://img.example/p4.jpg", 18.75, 4.2, 156, "aquarium filter", "pet_supplies", 4),
    ("B0TESTSP01", "Yoga Mat Non Slip Thick", "https://img.example/s1.jpg", 22.9, 4.5, 780, "yoga mat", "sports_outdoors", 1),
    ("B0TESTSP02", "Resistance Bands Set Fitness", "https://img.example/s2.jpg", 13.4, 4.3, 640, "resistance bands", "sports_outdoors", 2),
    ("B0TESTSP03", "Camping Lantern LED Rechargeable", "https://img.example/s3.jpg", 17.8, 4.1, 210, "camping lantern", "sports_outdoors", 3),
    ("B0TESTSP04", "Sport Water Bottle Insulated", "https://img.example/s4.jpg", 16.2, 4.4, 930, "sport water bottle", "sports_outdoors", 4),
]


def _ensure_database() -> None:
    try:
        psycopg.connect(TEST_DB_URL, connect_timeout=3).close()
        return
    except psycopg.OperationalError:
        pass
    with psycopg.connect(_ADMIN_URL, connect_timeout=3, autocommit=True) as conn:
        conn.execute('CREATE DATABASE "crossborder_test"')


def _apply_migrations() -> None:
    with psycopg.connect(TEST_DB_URL, connect_timeout=3) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.commit()
        exists = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='selection_runs'"
        ).fetchone()
        if exists:
            return
        for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            for stmt in sql_file.read_text(encoding="utf-8").split("--> statement-breakpoint"):
                if stmt.strip():
                    conn.execute(stmt)
        conn.commit()


def _seed_corpus() -> None:
    with psycopg.connect(TEST_DB_URL, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO corpus_products (asin,title,img,price_usd,rating,review_count,"
                "category_query,major_category,source_pos,scraped_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'2026-09-06T00:00:00') "
                "ON CONFLICT (asin) DO NOTHING",
                _CORPUS_SEED,
            )
        conn.commit()


def pytest_configure(config: pytest.Config) -> None:
    """收集期即验证 PG 可达（硬失败 + 指引），并准备测试库 schema。"""
    try:
        _ensure_database()
        _apply_migrations()
        _seed_corpus()
    except psycopg.OperationalError as e:
        raise RuntimeError(
            f"测试基座需要 Postgres@5433（PG-only）。"
            f"先运行 scripts\\pg_up.bat 再跑 pytest。原始错误：{e}"
        ) from e


@pytest.fixture(autouse=True)
def _clean_app_tables():
    """每个测试前清空应用表（corpus_* 种子保留）。"""
    with psycopg.connect(TEST_DB_URL, connect_timeout=3) as conn:
        conn.execute(f"TRUNCATE {', '.join(_APP_TABLES)} RESTART IDENTITY CASCADE")
        conn.commit()
    yield
