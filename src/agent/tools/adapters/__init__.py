"""Product data source adapters."""
from .base import ProductDataAdapter
from .local_json import LocalJSONAdapter

__all__ = ["ProductDataAdapter", "LocalJSONAdapter"]