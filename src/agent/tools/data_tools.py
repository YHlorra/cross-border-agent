"""High-level data query helpers wrapping the adapter.

Functions here are sync and accept a ``ProductDataAdapter`` so they can be
unit-tested with an in-memory adapter and called from nodes with the singleton
adapter injected via ``config["configurable"]["adapter"]``.
"""
from __future__ import annotations

from typing import Any

from .adapters.base import ProductDataAdapter


def search_1688_products(
    adapter: ProductDataAdapter,
    keyword: str,
    *,
    min_price: float | None = None,
    max_price: float | None = None,
) -> list[dict]:
    return adapter.search_1688(keyword, min_price=min_price, max_price=max_price)


def search_amazon_competitors(
    adapter: ProductDataAdapter, keyword: str
) -> list[dict]:
    return adapter.search_amazon(keyword)


def get_category_trend(adapter: ProductDataAdapter, category: str) -> dict[str, Any] | None:
    return adapter.get_category_trend(category)


def get_category_attributes(
    adapter: ProductDataAdapter, category: str
) -> dict[str, Any] | None:
    return adapter.get_category_attributes(category)