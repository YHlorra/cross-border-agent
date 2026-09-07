"""Live: Langfuse real-key path — gated by LLM_API_KEY + LANGFUSE_PUBLIC_KEY.

With keys set this fires one config_test generation end-to-end and flushes;
the console-side assertion (trace visible) is a manual acceptance step
recorded in the change's tasks.md 4.3.
"""
from __future__ import annotations

import os

import pytest

from agent import observability as obs
from agent.config_api import check_connection

pytestmark = pytest.mark.live


def _require_live() -> None:
    if not (
        os.environ.get("LLM_API_KEY") and os.environ.get("LANGFUSE_PUBLIC_KEY")
    ):
        pytest.skip("live: needs LLM_API_KEY + LANGFUSE_PUBLIC_KEY/SECRET_KEY")


async def test_config_test_generation_flushes_cleanly() -> None:
    _require_live()
    result = await check_connection(
        provider_name=os.environ.get("LLM_PRIMARY_PROVIDER", "deepseek"),
        api_key=os.environ["LLM_API_KEY"],
        model_id=os.environ.get("LLM_PRIMARY_MODEL", ""),
    )
    assert result["ok"] is True
    obs.flush()
