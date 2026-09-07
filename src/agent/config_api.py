"""LLM configuration page backend — provider catalog, model listing,
connection test, and env persistence with in-process hot reload.

All aimux exceptions propagate uncaught; the aiohttp layer converts them via
``errors.to_wire``. No retry, no fallback — same red-line as the main flow.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aimux

from . import observability as obs
from .llm import build_model_from_args

# ──── Curated provider catalog ────────────────────────────────────────────────

# Native factories ship as module-level callables in aimux.
# url is display-only — aimux uses its built-in endpoint for the call.
NATIVE_FACTORIES: dict[str, dict[str, str]] = {
    "openai": {"label": "OpenAI", "url": "https://api.openai.com/v1"},
    "anthropic": {"label": "Anthropic", "url": "https://api.anthropic.com"},
    "google": {"label": "Google", "url": "https://generativelanguage.googleapis.com"},
    "xai": {"label": "xAI", "url": "https://api.x.ai/v1"},
    "mistral": {"label": "Mistral", "url": "https://api.mistral.ai/v1"},
    "cohere": {"label": "Cohere", "url": "https://api.cohere.com/v2"},
    "deepseek": {"label": "DeepSeek", "url": "https://api.deepseek.com"},
}

# Registry-backed OpenAI-compatible providers — these support list_models.
REGISTRY_PROVIDERS: dict[str, dict[str, str]] = {
    "groq": {"label": "Groq", "url": "https://api.groq.com/openai/v1"},
    "moonshotai": {"label": "Moonshot AI", "url": "https://api.moonshot.ai/v1"},
    "moonshotai_cn": {"label": "Moonshot AI CN", "url": "https://api.moonshot.cn/v1"},
    "minimax": {"label": "MiniMax", "url": "https://api.minimax.io/v1"},
    "minimax_cn": {"label": "MiniMax CN", "url": "https://api.minimaxi.com/v1"},
    "baidu": {"label": "百度文心", "url": "https://qianfan.baidubce.com/v2"},
    "baidu_v2": {"label": "百度文心 v2", "url": "https://qianfan.baidubce.com/v2"},
    "alibaba": {"label": "阿里通义", "url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    "doubao": {"label": "豆包", "url": "https://ark.cn-beijing.volces.com/api/v3"},
    "bigmodel": {"label": "智谱 GLM", "url": "https://open.bigmodel.cn/api/paas/v4"},
    "baichuan": {"label": "百川", "url": "https://api.baichuan-ai.com/v1"},
    "lingyiwanwu": {"label": "零一万物", "url": "https://api.lingyiwanwu.com/v1"},
    "kimi": {"label": "Kimi", "url": "https://api.moonshot.cn/v1"},
    "modelscope": {"label": "ModelScope", "url": "https://api-inference.modelscope.cn/v1"},
    "siliconflow": {"label": "硅基流动", "url": "https://api.siliconflow.cn/v1"},
    "perplexity": {"label": "Perplexity", "url": "https://api.perplexity.ai"},
    "fireworks": {"label": "Fireworks", "url": "https://api.fireworks.ai/inference/v1"},
    "github": {"label": "GitHub Models", "url": "https://models.github.ai/inference"},
}


def list_providers() -> list[dict[str, Any]]:
    """Return the curated provider catalog.

    Each entry carries ``can_list_models`` — native factories lack a
    ``ProviderHandle`` so their model ids must be typed manually. ``url`` is
    display-only for the preset cards.
    """
    out: list[dict[str, Any]] = []
    for name, meta in NATIVE_FACTORIES.items():
        out.append(
            {
                "name": name,
                "label": meta["label"],
                "url": meta["url"],
                "category": "native",
                "can_list_models": False,
            }
        )
    for name, meta in REGISTRY_PROVIDERS.items():
        out.append(
            {
                "name": name,
                "label": meta["label"],
                "url": meta["url"],
                "category": "registry",
                "can_list_models": True,
            }
        )
    return out


def list_models(
    provider_name: str,
    api_key: str,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    """List real models for a registry provider via ``ProviderHandle.list_models``.

    Calling this doubles as an API-key validity check: a bad key surfaces as
    ``APICallError`` with the upstream status / body intact.
    """
    with obs.generation({"name": "list_models"}, name="list_models", model=None, provider=provider_name):
        handle = aimux.create_provider(
            name=provider_name,
            api_key=api_key,
            base_url=base_url,
        )
        raw = handle.list_models()
    return _normalize_model_list(raw)


def _normalize_model_list(raw: Any) -> list[dict[str, Any]]:
    """Accept the provider-specific list_models shapes and flatten to
    {id, name} items. Unknown shapes degrade to an empty list rather than
    crashing the config page."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, dict):
        # {"models": [...]} / {"data": [...]} wrapper
        raw = raw.get("models") or raw.get("data") or raw.get("object") or []
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            out.append({"id": item, "name": item})
        elif isinstance(item, dict):
            mid = item.get("id") or item.get("model_id") or item.get("name")
            if mid:
                out.append(
                    {"id": str(mid), "name": str(item.get("name") or mid)}
                )
    return out


def check_connection(
    provider_name: str,
    api_key: str,
    model_id: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Fire a minimal generation request to prove end-to-end connectivity.

    Returns the model's short reply plus token usage. Upstream failures
    propagate as APICallError etc.
    """
    model = build_model_from_args(provider_name, api_key, model_id, base_url)
    with obs.generation(
        {"name": "config_test"}, name="config_test", model=model_id, provider=provider_name
    ) as gen:
        result = aimux.generate_text(
            model,
            "Reply with the single word: ok",
            {"max_output_tokens": 8, "temperature": 0},
        )
        gen.update(output=(result.get("text") or "")[:200])
    text = (result.get("text") or "").strip()
    return {
        "ok": True,
        "reply": text[:200],
        "usage": result.get("usage"),
        "model": model_id,
    }


# ──── Env persistence ─────────────────────────────────────────────────────────

def env_file_path() -> Path:
    """Location of the runtime env file, relative to the project root."""
    return Path(__file__).resolve().parents[2] / ".env.local"


def _read_env_file() -> dict[str, str]:
    path = env_file_path()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def save_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist the config page form to .env.local and update the in-process
    env so a hot reload picks it up without restarting the bridge.

    ``api_key`` may be empty — the existing env key is then preserved, which
    lets the "activate provider" action swap models without re-entering a key.
    """
    required = [
        "primary_provider",
        "primary_model",
        "cheap_provider",
        "cheap_model",
    ]
    missing = [k for k in required if not payload.get(k)]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    env = _read_env_file()
    api_key = (payload.get("api_key") or "").strip()
    if api_key:
        env["LLM_API_KEY"] = api_key
    elif not env.get("LLM_API_KEY"):
        raise ValueError("api_key is required when none is configured yet")

    env["LLM_PRIMARY_PROVIDER"] = str(payload["primary_provider"]).strip()
    env["LLM_PRIMARY_MODEL"] = str(payload["primary_model"]).strip()
    env["LLM_CHEAP_PROVIDER"] = str(payload["cheap_provider"]).strip()
    env["LLM_CHEAP_MODEL"] = str(payload["cheap_model"]).strip()
    base_url = (payload.get("base_url") or "").strip()
    if base_url:
        env["LLM_BASE_URL"] = base_url
    else:
        env.pop("LLM_BASE_URL", None)

    lines = [f"{k}={v}" for k, v in env.items()]
    env_file_path().write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "written_to": str(env_file_path()),
        "env": env,
        "snapshot": {
            "primary_provider": env.get("LLM_PRIMARY_PROVIDER"),
            "primary_model": env.get("LLM_PRIMARY_MODEL"),
            "cheap_provider": env.get("LLM_CHEAP_PROVIDER"),
            "cheap_model": env.get("LLM_CHEAP_MODEL"),
            "base_url": env.get("LLM_BASE_URL") or None,
            "api_key_set": bool(env.get("LLM_API_KEY")),
        },
    }


__all__ = [
    "list_providers",
    "list_models",
    "check_connection",
    "save_config",
    "env_file_path",
]
