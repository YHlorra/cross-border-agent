""" — list_listing_runs / get_listing_run SEAM tests (Listing 列表).

Pure DB SEAM — no LLM, no network, no aiohttp. Covers:
- empty table → []
- normal rows newest-first with product_name/hard_failed summary extraction
- corrupt listing_json rows degrade to empty-name summaries (never raise)
- limit clamps the row count
- get_listing_run returns the full envelope
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.persistence import (
    get_listing_run,
    list_listing_runs,
    save_listing_run,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "listing.db"


_CANDIDATE = {
    "product_id": "p1",
    "name_cn": "猫爬架",
    "name_en": "Cat Tree",
    "source_price_cny": 120.0,
    "target_price_usd": 39.99,
    "price_gap_ratio": 1.2,
    "category": "宠物",
    "review_count": 10,
}


def _envelope(name_en: str = "Cat Tree", hard_failed: bool = False) -> dict:
    return {
        "listing": {
            "item_name": f"{name_en} Premium",
            "bullet_point": [],
            "product_description": "d",
            "generic_keyword": "cat tree",
        },
        "issues": [],
        "hard_failed": hard_failed,
        "candidate": {**_CANDIDATE, "name_en": name_en},
    }


def test_list_empty_table_returns_empty(db: Path):
    assert list_listing_runs(db_path=db) == []


def test_list_rows_newest_first_with_summary(db: Path):
    for i in range(3):
        save_listing_run(
            session_id=f"s{i}",
            product_id=f"p{i}",
            market_code="US",
            listing=_envelope(name_en=f"Item{i}"),
            run_id=f"r{i}",
            db_path=db,
        )
    rows = list_listing_runs(db_path=db)
    assert [r["id"] for r in rows] == ["r2", "r1", "r0"]
    assert rows[0]["product_name"] == "Item2"
    assert rows[0]["hard_failed"] is False
    assert rows[0]["market_code"] == "US"
    assert rows[0]["session_id"] == "s2"


def test_list_hard_failed_flag_extracted(db: Path):
    save_listing_run(
        session_id="s1",
        product_id="p1",
        market_code="US",
        listing=_envelope(hard_failed=True),
        run_id="r1",
        db_path=db,
    )
    rows = list_listing_runs(db_path=db)
    assert rows[0]["hard_failed"] is True


def test_list_corrupt_json_degrades_not_raises(db: Path):
    # Direct insert of a corrupt row (bypassing save_listing_run's serialization).
    save_listing_run(
        session_id="s-ok",
        product_id="p-ok",
        market_code="US",
        listing=_envelope(),
        run_id="r-ok",
        db_path=db,
    )
    from agent.persistence.pg_store import _conn

    with _conn(db) as conn:
        conn.execute(
            """INSERT INTO listing_runs (id, session_id, product_id, market_code, listing_json, created_at)
               VALUES ('r-bad', 's-bad', 'p-bad', 'US', 'not-json{', '2026-09-01T00:00:00+00:00')"""
        )
        conn.commit()

    rows = list_listing_runs(db_path=db)
    by_id = {r["id"]: r for r in rows}
    assert by_id["r-bad"]["product_name"] == ""
    assert by_id["r-bad"]["hard_failed"] is False
    assert by_id["r-ok"]["product_name"] == "Cat Tree"


def test_list_limit(db: Path):
    for i in range(5):
        save_listing_run(
            session_id="s",
            product_id=f"p{i}",
            market_code="US",
            listing=_envelope(),
            run_id=f"r{i}",
            db_path=db,
        )
    rows = list_listing_runs(limit=2, db_path=db)
    assert [r["id"] for r in rows] == ["r4", "r3"]


def test_get_listing_run_returns_full_envelope(db: Path):
    env = _envelope()
    save_listing_run(
        session_id="s1",
        product_id="p1",
        market_code="US",
        listing=env,
        run_id="r1",
        db_path=db,
    )
    run = get_listing_run("r1", db_path=db)
    assert run is not None
    assert run["id"] == "r1"
    assert run["session_id"] == "s1"
    assert run["listing"]["listing"]["item_name"] == "Cat Tree Premium"
    assert run["listing"]["candidate"]["name_en"] == "Cat Tree"
    assert run["listing"]["hard_failed"] is False


def test_get_listing_run_unknown_id_returns_none(db: Path):
    assert get_listing_run("nope", db_path=db) is None
