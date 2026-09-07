"""Empty-stub adapter — kept as the parent class for ``PgCorpusAdapter``.

History (selection-cleanup 2026-09-06): this module used to read
``data/selection/{1688_products,amazon_competitors,category_trends,
category_attributes}.json`` — hand-authored demo fixtures that drifted out of
sync with the real PG corpus. The user removed the JSON fixtures; PG
``corpus_products`` is now the only mock data source for selection.

We keep the class because ``PgCorpusAdapter`` inherits from it and tests pass
``adapter=LocalJSONAdapter()`` to pin registry shape. All four data methods
return ``[]`` / ``None`` so the registry can still wire and surface the
honest-empty signal in reports; ``PgCorpusAdapter.search_amazon`` overrides
this method to read from PG and is the production path.

When a real 1688 / Amazon / trend / attribute source is wired later
(Sorftime MCP gate), implement it as a separate adapter
class — do not resurrect this stub as a data carrier.
"""
from __future__ import annotations

from typing import Any

from .base import ProductDataAdapter


class LocalJSONAdapter(ProductDataAdapter):
    """No-op adapter — every data method returns an empty result.

    Inherits the ``ProductDataAdapter`` contract so the agent tool registry
    can be instantiated against this stub in tests; production goes through
    ``PgCorpusAdapter`` (PG corpus) for ``search_amazon`` and honest-empty
    for everything else until real sources are wired.
    """

    def __init__(self, data_dir: Any = None) -> None:
        # ``data_dir`` accepted for backward compatibility with old call sites
        # that passed a fixture directory; ignored now.
        return

    def search_1688(
        self, keyword: str, *, min_price: float | None = None, max_price: float | None = None
    ) -> list[dict]:
        return []

    def search_amazon(self, keyword: str) -> list[dict]:
        return []

    def get_category_trend(self, category: str) -> dict | None:
        return None

    def get_category_attributes(self, category: str) -> dict | None:
        return None