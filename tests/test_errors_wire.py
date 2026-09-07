"""SEAM tests: errors.to_wire wire-format contract ( path foundation).

The frontend ErrorView branches on ``code`` / ``missing`` / ``message`` —
these tests pin the wire contract for all three error classes so the
transparency red line (no masking, no transformation) cannot regress.
No LLM involved — pure serialization tests.
"""
from __future__ import annotations

import json

import aimux

from agent.errors import to_wire
from agent.llm import StartupConfigError


def test_startup_config_error_wire() -> None:
    wire = to_wire(StartupConfigError(["LLM_API_KEY"]))
    assert wire["event"] == "error"
    assert wire["code"] == "StartupConfigError"
    assert wire["missing"] == ["LLM_API_KEY"]
    assert "LLM_API_KEY" in wire["message"]


def test_startup_config_error_preserves_all_missing_vars() -> None:
    wire = to_wire(StartupConfigError(["LLM_PRIMARY_PROVIDER", "LLM_CHEAP_MODEL"]))
    assert wire["missing"] == ["LLM_PRIMARY_PROVIDER", "LLM_CHEAP_MODEL"]


def test_aimux_api_call_error_wire() -> None:
    err = aimux.APICallError("upstream 401: Incorrect API key provided")
    wire = to_wire(err)
    assert wire["event"] == "error"
    assert wire["code"] == "APICallError"
    assert "Incorrect API key" in wire["message"]


def test_generic_exception_wire() -> None:
    wire = to_wire(ValueError("boom"))
    assert wire["event"] == "error"
    assert wire["code"] == "ValueError"
    assert wire["message"] == "boom"
    assert wire["status"] is None


def test_all_wire_payloads_are_json_serializable() -> None:
    # server.py does json.dumps(to_wire(exc)) on the streaming path — a
    # non-serializable field here would turn a clean error event into a 500.
    for exc in (
        StartupConfigError(["LLM_API_KEY"]),
        aimux.APICallError("429 rate limited"),
        ValueError("boom"),
    ):
        json.dumps(to_wire(exc))
