"""Unit tests for the saved-provider store."""
from __future__ import annotations

import pytest

from agent import providers_store


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    """Point the store at a temp file for every test."""
    monkeypatch.setattr(
        providers_store, "_FILE", tmp_path / "providers.json"
    )


def test_save_and_list_roundtrip():
    providers_store.save(
        {
            "name": "deepseek",
            "label": "DeepSeek",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-secret-1234",
            "models": [{"id": "deepseek-chat", "name": "deepseek-chat"}],
            "category": "registry",
        }
    )
    saved = providers_store.list_saved()
    assert len(saved) == 1
    entry = saved[0]
    assert entry["name"] == "deepseek"
    assert entry["api_key_set"] is True
    assert entry["api_key_tail"] == "1234"
    assert "api_key" not in entry  # secret never exposed


def test_save_requires_name_and_key():
    with pytest.raises(ValueError):
        providers_store.save({"api_key": "x"})
    with pytest.raises(ValueError):
        providers_store.save({"name": "x"})


def test_upsert_same_name_updates():
    providers_store.save(
        {"name": "a", "label": "A", "api_key": "key-one-long"}
    )
    providers_store.save(
        {"name": "a", "label": "A2", "api_key": "key-two-long", "models": []}
    )
    saved = providers_store.list_saved()
    assert len(saved) == 1
    assert saved[0]["api_key_tail"] == "long"
    assert saved[0]["label"] == "A2"


def test_delete():
    providers_store.save({"name": "a", "api_key": "k1"})
    providers_store.save({"name": "b", "api_key": "k2"})
    assert providers_store.delete("a") is True
    assert providers_store.delete("a") is False
    assert [s["name"] for s in providers_store.list_saved()] == ["b"]


def test_model_config_default_and_override(tmp_path, monkeypatch):
    from agent.providers_store import (
        effective_agent_model,
        load_model_config,
        save_model_config,
    )
    monkeypatch.setattr(providers_store, "_MODEL_FILE", tmp_path / "model_config.json")
    save_model_config(
        {
            "default": {"provider": "deepseek", "model": "deepseek-chat"},
            "agents": {
                "selection": {"provider": "minimax_cn", "model": "MiniMax-Text-01"},
                "listing": None,
            },
        }
    )
    cfg = load_model_config()
    assert effective_agent_model(cfg, "selection")["model"] == "MiniMax-Text-01"
    assert effective_agent_model(cfg, "listing")["model"] == "deepseek-chat"
    assert effective_agent_model(cfg, "monitor")["provider"] == "deepseek"


def test_model_config_requires_default(tmp_path, monkeypatch):
    from agent.providers_store import save_model_config
    monkeypatch.setattr(providers_store, "_MODEL_FILE", tmp_path / "model_config.json")
    with pytest.raises(ValueError):
        save_model_config({"default": {}, "agents": {}})


def test_find_api_key_looks_up_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(providers_store, "_FILE", tmp_path / "providers.json")
    providers_store.save({"name": "deepseek", "api_key": "sk-ds-key"})
    assert providers_store.find_api_key("deepseek") == "sk-ds-key"
    assert providers_store.find_api_key("nope") is None
