"""Adapter ABC + supporting dataclasses for product data sources."""
from __future__ import annotations

from abc import ABC, abstractmethod


class ProductDataAdapter(ABC):
    """Read-only product/competitor/trend data source.

    Real implementations will wrap Sorftime MCP / 1688-cli / Amazon SP-API. The
    fixture-backed LocalJSONAdapter covers demo and tests.
    """

    @abstractmethod
    def search_1688(
        self, keyword: str, *, min_price: float | None = None, max_price: float | None = None
    ) -> list[dict]: ...

    @abstractmethod
    def search_amazon(self, keyword: str) -> list[dict]: ...

    @abstractmethod
    def get_category_trend(self, category: str) -> dict | None: ...

    @abstractmethod
    def get_category_attributes(self, category: str) -> dict | None: ...