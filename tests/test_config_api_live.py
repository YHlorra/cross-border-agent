"""Live config-page round-trip — gated on LLM_API_KEY env var.

Drives the real aimux path: model listing from the configured registry
provider and a minimal connection test. Skips when no key is present.
"""
from __future__ import annotations

import os

import pytest

from agent.config_api import list_models, check_connection

pytestmark = pytest.mark.live


def _require_env() -> None:
    required = ["LLM_API_KEY", "LLM_PRIMARY_PROVIDER", "LLM_PRIMARY_MODEL"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        pytest.skip(f"missing env: {', '.join(missing)}")


def test_list_models_real():
    _require_env()
    provider = os.environ["LLM_PRIMARY_PROVIDER"]
    models = list_models(provider, os.environ["LLM_API_KEY"])
    assert isinstance(models, list)
    # The configured primary model must appear in the real catalog.
    assert any(m["id"] == os.environ["LLM_PRIMARY_MODEL"] for m in models)


def test_connection_real():
    _require_env()
    provider = os.environ["LLM_PRIMARY_PROVIDER"]
    result = check_connection(
        provider,
        os.environ["LLM_API_KEY"],
        os.environ["LLM_PRIMARY_MODEL"],
    )
    assert result["ok"] is True
    assert result["reply"]  # non-empty model reply
