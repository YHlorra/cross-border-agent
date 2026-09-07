"""Persistent store for configured LLM providers.

Stores multiple provider records — preset providers with an api key, and
custom providers with their own OpenAI-compatible URL. Backed by a JSON file
so the config page left rail survives restarts.

The active primary/cheap selection still lives in .env.local via
``save_config``; this file only tracks the *catalog* of known providers.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

# AGENT_DATA_DIR lets tests / deployments boot the server against an isolated
# config directory (e.g. an empty temp dir to exercise the no-key startup
# path) without touching real provider credentials in data/selection/.
_DATA_DIR = Path(
    os.environ.get("AGENT_DATA_DIR")
    or (Path(__file__).resolve().parents[2] / "data" / "selection")
)
_FILE = _DATA_DIR / "providers.json"

_lock = threading.Lock()


def _read() -> list[dict[str, Any]]:
    if not _FILE.exists():
        return []
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write(records: list[dict[str, Any]]) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_saved() -> list[dict[str, Any]]:
    """All saved providers with the api key masked."""
    out = []
    for r in _read():
        out.append(_public(r))
    return out


def _public(record: dict[str, Any]) -> dict[str, Any]:
    key = record.get("api_key") or ""
    masked = key[-4:] if len(key) > 4 else "****"
    return {
        "name": record.get("name"),
        "label": record.get("label"),
        "base_url": record.get("base_url"),
        "api_key_set": bool(key),
        "api_key_tail": masked,
        "models": record.get("models") or [],
        "category": record.get("category", "custom"),
    }


def save(record: dict[str, Any]) -> dict[str, Any]:
    """Upsert a provider record by name. Requires name and api_key."""
    name = (record.get("name") or "").strip()
    key = (record.get("api_key") or "").strip()
    if not name:
        raise ValueError("name is required")
    if not key:
        raise ValueError("api_key is required")

    with _lock:
        records = _read()
        found = next((r for r in records if r.get("name") == name), None)
        if found:
            found.update(record)
            found["name"] = name
            found["api_key"] = key
        else:
            records.append(record)
        _write(records)
    return _public(record)


def delete(name: str) -> bool:
    """Remove a provider by name. Returns True if it existed."""
    with _lock:
        records = _read()
        before = len(records)
        records = [r for r in records if r.get("name") != name]
        if len(records) == before:
            return False
        _write(records)
    return True


# ──── Model role config ──────────────────────────────────────────────────────
# Default model + per-agent overrides, stored alongside the provider catalog.

_MODEL_FILE = _FILE.parent / "model_config.json"

# Business stages (agents) that can each have their own model.
AGENT_KEYS: list[str] = ["selection", "listing", "monitor", "store"]


def _default_model_config() -> dict[str, Any]:
    return {
        "default": {"provider": None, "model": None, "base_url": None},
        "agents": {k: None for k in AGENT_KEYS},
        # dynamic per-model window settings
        "compaction": {
            "enabled": False,
            "reserve_tokens": 8_192,
            "keep_recent_tokens": 4_096,
            "fallback_context_window": 32_000,
            "per_model_context_windows": {},
        },
    }


def load_model_config() -> dict[str, Any]:
    if not _MODEL_FILE.exists():
        return _default_model_config()
    try:
        data = json.loads(_MODEL_FILE.read_text(encoding="utf-8"))
        base = _default_model_config()
        if isinstance(data, dict):
            # shallow update top-level keys; nested blocks (default, agents,
            # compaction) merge so missing subkeys still default.
            for k, v in data.items():
                if isinstance(v, dict) and isinstance(base.get(k), dict):
                    base[k].update(v)
                else:
                    base[k] = v
        return base
    except (json.JSONDecodeError, OSError):
        return _default_model_config()


def save_model_config(config: dict[str, Any]) -> dict[str, Any]:
    """Persist the model role config: default + per-agent overrides + compaction.

    Each entry is {provider, model, base_url} or None for agents (None =
    inherit the default). The compaction block is preserved
    verbatim to round-trip compression settings.
    """
    default = config.get("default") or {}
    if not default.get("provider") or not default.get("model"):
        raise ValueError("default provider and model are required")

    clean: dict[str, Any] = {
        "default": {
            "provider": str(default["provider"]),
            "model": str(default["model"]),
            "base_url": (default.get("base_url") or "").strip() or None,
        },
        "agents": {},
        "compaction": _normalize_compaction(
            config.get("compaction") or {}
        ),
    }
    agents = config.get("agents") or {}
    for key in AGENT_KEYS:
        entry = agents.get(key)
        if entry and entry.get("provider") and entry.get("model"):
            clean["agents"][key] = {
                "provider": str(entry["provider"]),
                "model": str(entry["model"]),
                "base_url": (entry.get("base_url") or "").strip() or None,
            }
        else:
            clean["agents"][key] = None

    with _lock:
        _MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MODEL_FILE.write_text(
            json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return clean


def _normalize_compaction(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce compaction block into a clean shape for persistence."""
    defaults = _default_model_config()["compaction"]
    out = {k: raw.get(k, defaults[k]) for k in defaults}
    # per_model_context_windows: ensure {model_name: int} mapping
    per = raw.get("per_model_context_windows") or {}
    out["per_model_context_windows"] = {
        str(k): int(v) for k, v in per.items() if v
    }
    return out


def load_compaction_settings(stage: str | None = None) -> dict[str, Any]:
    """Convenience: just the compaction block, with effective_window resolved
    for the given stage's model (or default model if stage is None).
    """
    cfg = load_model_config()
    block = cfg.get("compaction") or _default_model_config()["compaction"]
    # resolve model for the stage (override > default)
    if stage:
        entry = effective_agent_model(cfg, stage)
    else:
        entry = cfg.get("default") or {}
    model_name = entry.get("model")
    block = dict(block)
    block["context_window"] = int(
        block.get("per_model_context_windows", {}).get(model_name)
        or block.get("fallback_context_window", 32_000)
    )
    return block


def effective_agent_model(
    config: dict[str, Any], agent_key: str
) -> dict[str, Any]:
    """Resolve the model an agent actually uses: override wins, else default."""
    override = (config.get("agents") or {}).get(agent_key)
    if override and override.get("provider") and override.get("model"):
        return override
    return config.get("default") or {}


def find_api_key(provider_name: str) -> str | None:
    """Look up a saved provider's api key by provider name."""
    for r in _read():
        if r.get("name") == provider_name:
            return r.get("api_key")
    return None


__all__ = [
    "list_saved",
    "save",
    "delete",
    "load_model_config",
    "save_model_config",
    "effective_agent_model",
    "find_api_key",
    "AGENT_KEYS",
]
