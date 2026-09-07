"""Auto session title generation SEAM.

Pure tests over the title generation module. We mock ``aimux.generate_text``
at the boundary the production code uses (asyncio.to_thread → aimux), so
the SEAM exercises the structured-output + sanitization + len-capping
pipeline without touching a real model.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from agent.llm import AimuxChatModel
from agent.persistence import get_session_meta, init_db, update_session_meta
from agent.agents.title import generate_session_title


# ─── helpers ──────────────────────────────────────────────────────────────────


class _FakeAimuxText:
    """Mimic the aimux.generate_text return shape (dict with 'text')."""

    def __init__(self, text: str) -> None:
        self.text = text
        # The production path in llm.structured only reads the dict
        # branch; we provide the same shape to stay faithful.
        self._raw = {"text": text, "tool_calls": [], "usage": None,
                     "warnings": [], "finish_reason": {"unified": "stop"}}

    def get(self, key: str, default: Any = None) -> Any:
        return self._raw.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._raw[key]


def _patch_aimux(monkeypatch, payload: dict | str | Exception) -> None:
    """Stub aimux.generate_text → returns the canned dict.

    Pass an Exception instance to simulate a model failure.
    Pass a string for a raw text response (will be JSON-parsed downstream).
    """
    def _fake(model: Any, prompt: list, opts: dict | None = None):
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, str):
            # Bare text — mimic the {"text": "..."} envelope
            return {"text": payload, "tool_calls": [], "usage": None,
                    "warnings": [], "finish_reason": {"unified": "stop"}}
        return {"text": json.dumps(payload, ensure_ascii=False),
                "tool_calls": [], "usage": None, "warnings": [],
                "finish_reason": {"unified": "stop"}}

    monkeypatch.setattr("aimux.generate_text", _fake)


def _make_model() -> AimuxChatModel:
    return AimuxChatModel(model=object())  # model obj is never touched (stubbed)


# ─── happy path ───────────────────────────────────────────────────────────────


def test_generate_session_title_strips_trailing_punct(monkeypatch) -> None:
    _patch_aimux(monkeypatch, {"title": "宠物用品选品。"})
    title = asyncio.run(generate_session_title(_make_model(), "宠物用品，预算 5000"))
    assert title == "宠物用品选品"


def test_generate_session_title_caps_at_max_chars(monkeypatch) -> None:
    _patch_aimux(monkeypatch, {"title": "这是一段超长标题" * 4})  # 32 chars
    title = asyncio.run(generate_session_title(_make_model(), "什么都能卖吗？", max_chars=16))
    assert title is not None
    assert len(title) <= 16


def test_generate_session_title_preserves_chinese(monkeypatch) -> None:
    _patch_aimux(monkeypatch, {"title": "无线耳机选品"})
    title = asyncio.run(generate_session_title(_make_model(), "无线耳机有什么好卖的？"))
    assert title == "无线耳机选品"


# ─── failure modes (all silent) ───────────────────────────────────────────────


def test_generate_session_title_returns_none_on_empty_query() -> None:
    out = asyncio.run(generate_session_title(_make_model(), "   "))
    assert out is None


def test_generate_session_title_returns_none_on_aimux_error(monkeypatch) -> None:
    _patch_aimux(monkeypatch, RuntimeError("upstream down"))
    out = asyncio.run(generate_session_title(_make_model(), "无线耳机"))
    assert out is None


def test_generate_session_title_returns_none_on_bad_json(monkeypatch) -> None:
    _patch_aimux(monkeypatch, "not json at all")
    out = asyncio.run(generate_session_title(_make_model(), "无线耳机"))
    assert out is None


def test_generate_session_title_returns_none_on_empty_string(monkeypatch) -> None:
    _patch_aimux(monkeypatch, {"title": "   "})
    out = asyncio.run(generate_session_title(_make_model(), "无线耳机"))
    assert out is None


def test_generate_session_title_returns_none_on_schema_mismatch(monkeypatch) -> None:
    # Wrong field name — pydantic will reject.
    _patch_aimux(monkeypatch, {"name": "宠物"})
    out = asyncio.run(generate_session_title(_make_model(), "宠物用品"))
    assert out is None


# ─── write idempotency (mirrors server._spawn_title_task behaviour) ──────────


def test_title_write_is_idempotent(tmp_path: Path) -> None:
    """Mirrors the server's race check: a title that already exists is
    never overwritten, even if the LLM proposes something new."""
    db = tmp_path / "title.db"
    init_db(db_path=db)
    update_session_meta(session_id="s1", fields={"title": "我手动起的"}, db_path=db)

    # Simulate the second-write guard: get_session_meta.title is non-empty
    # → the server skips update_session_meta entirely.
    if not get_session_meta(session_id="s1", db_path=db).get("title"):
        update_session_meta(
            session_id="s1", fields={"title": "LLM 后来的"}, db_path=db
        )
    assert get_session_meta(session_id="s1", db_path=db)["title"] == "我手动起的"


def test_title_write_runs_when_empty(tmp_path: Path) -> None:
    db = tmp_path / "title.db"
    init_db(db_path=db)
    # no prior title
    if not get_session_meta(session_id="s1", db_path=db).get("title"):
        update_session_meta(
            session_id="s1", fields={"title": "宠物用品选品"}, db_path=db
        )
    assert get_session_meta(session_id="s1", db_path=db)["title"] == "宠物用品选品"
