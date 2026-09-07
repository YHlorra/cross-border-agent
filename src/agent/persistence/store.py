"""Persistence layer — Postgres only。

SQLite 实现已删除；原双驱动 dispatcher 与
sqlite-vec 路径一并退役）。本模块只是转发：公开名与 pg_store 完全一致，
数据目录解析在 paths.py。DATABASE_URL 为启动必填（缺省即 _url 抛错）。
"""
from __future__ import annotations

from . import pg_store
from .paths import resolve_data_dir
from .pg_store import *  # noqa: F401,F403 — 唯一实现（签名即契约）

__all__ = [*pg_store.__all__, "resolve_data_dir"]
