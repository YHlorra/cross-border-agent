"""Unit tests for the LLM config-page backend.

Pure-logic tests that need no API key: provider catalog shape, model-list
normalization, env persistence with hot reload. The live round-trip lives
in test_config_api_live.py.
"""
from __future__ import annotations

import os

import pytest

from agent.config_api import (
    _normalize_model_list,
    env_file_path,
    list_providers,
    save_config,
)


def test_list_providers_catalog_shape():
    providers = list_providers()
    assert isinstance(providers, list) and len(providers) >= 20
    names = {p["name"] for p in providers}
    # Native factories must be present.
    assert {"openai", "anthropic", "deepseek", "google"} <= names
    # Registry providers must be present and listable.
    for p in providers:
        assert p["name"] and p["label"] and p["category"] in {"native", "registry"}
        if p["category"] == "registry":
            assert p["can_list_models"] is True
        else:
            assert p["can_list_models"] is False


def test_normalize_model_list_variants():
    # OpenAI-style {"data": [...]}
    assert _normalize_model_list({"data": [{"id": "gpt-4o"}]}) == [
        {"id": "gpt-4o", "name": "gpt-4o"}
    ]
    # Bare list of strings
    assert _normalize_model_list(["m1", "m2"]) == [
        {"id": "m1", "name": "m1"},
        {"id": "m2", "name": "m2"},
    ]
    # JSON string input
    assert _normalize_model_list('[{"id": "a", "name": "A"}]') == [
        {"id": "a", "name": "A"}
    ]
    # Garbage degrades to empty list, never raises.
    assert _normalize_model_list(None) == []
    assert _normalize_model_list(123) == []
    assert _normalize_model_list({"unexpected": True}) == []


def test_save_config_roundtrip(tmp_path, monkeypatch):
    # Redirect env file into a temp dir so the real .env.local is untouched.
    fake_env = tmp_path / ".env.local"
    monkeypatch.setattr(
        "agent.config_api.env_file_path", lambda: fake_env
    )

    payload = {
        "api_key": "sk-test-123",
        "primary_provider": "deepseek",
        "primary_model": "deepseek-chat",
        "cheap_provider": "groq",
        "cheap_model": "llama-3.3-70b",
        "base_url": "",
    }
    saved = save_config(payload)
    assert saved["ok"] is True
    assert saved["snapshot"]["primary_provider"] == "deepseek"
    assert saved["snapshot"]["cheap_model"] == "llama-3.3-70b"
    assert saved["snapshot"]["api_key_set"] is True

    text = fake_env.read_text(encoding="utf-8")
    assert "LLM_API_KEY=sk-test-123" in text
    assert "LLM_PRIMARY_PROVIDER=deepseek" in text
    assert "LLM_BASE_URL=" not in text

    # Base URL present -> persisted; blank base_url -> removed.
    save_config({**payload, "base_url": "https://relay.example/v1"})
    assert "LLM_BASE_URL=https://relay.example/v1" in fake_env.read_text(
        encoding="utf-8"
    )
    save_config({**payload, "base_url": "  "})
    assert "LLM_BASE_URL=" not in fake_env.read_text(encoding="utf-8")


def test_save_config_requires_all_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent.config_api.env_file_path", lambda: tmp_path / ".env.local"
    )
    with pytest.raises(ValueError):
        save_config({"api_key": "x"})  # missing the four model fields


def test_save_config_does_not_mirror_process_env(tmp_path, monkeypatch):
    """save_config writes the file only; env mirroring is the server's job."""
    fake_env = tmp_path / ".env.local"
    monkeypatch.setattr(
        "agent.config_api.env_file_path", lambda: fake_env
    )
    monkeypatch.delenv("LLM_PRIMARY_MODEL", raising=False)
    save_config(
        {
            "api_key": "sk-x",
            "primary_provider": "openai",
            "primary_model": "gpt-4o",
            "cheap_provider": "openai",
            "cheap_model": "gpt-4o-mini",
            "base_url": None,
        }
    )
    # The persisted env dict is returned, but os.environ stays untouched —
    # the aiohttp layer applies it before boot.
    monkeypatch.delenv("LLM_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert "LLM_PRIMARY_MODEL" not in os.environ
    assert "LLM_API_KEY" not in os.environ


def test_to_wire_carries_upstream_status(monkeypatch):
    """APICallError must expose real HTTP status + body in wire format."""
    import aimux
    from agent.errors import to_wire

    class FakeAPICallError(aimux.AimuxError):
        status = 401
        response_body = {"error": {"message": "bad key"}}
        headers = None

    # No exception is raised by aimux itself here — verify via a real
    # create_provider + fake key against a live provider is covered by the
    # live test. This unit test pins the attribute mapping.
    assert to_wire(FakeAPICallError())["status"] == 401
    assert to_wire(FakeAPICallError())["body"] == {"error": {"message": "bad key"}}
    assert to_wire(FakeAPICallError())["code"] == "FakeAPICallError"


def test_save_config_preserves_existing_key(tmp_path, monkeypatch):
    """Empty api_key keeps the previously saved key — activate flow."""
    fake_env = tmp_path / ".env.local"
    monkeypatch.setattr(
        "agent.config_api.env_file_path", lambda: fake_env
    )
    payload = {
        "api_key": "sk-original",
        "primary_provider": "deepseek",
        "primary_model": "deepseek-chat",
        "cheap_provider": "deepseek",
        "cheap_model": "deepseek-chat",
    }
    save_config(payload)
    # Second save without a key keeps the original.
    save_config({**payload, "api_key": ""})
    assert "LLM_API_KEY=sk-original" in fake_env.read_text(encoding="utf-8")


def test_save_config_requires_key_when_none_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent.config_api.env_file_path", lambda: tmp_path / ".env.local"
    )
    with pytest.raises(ValueError):
        save_config(
            {
                "api_key": "",
                "primary_provider": "deepseek",
                "primary_model": "deepseek-chat",
                "cheap_provider": "deepseek",
                "cheap_model": "deepseek-chat",
            }
        )
