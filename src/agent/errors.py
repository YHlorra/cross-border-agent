"""Translate internal exceptions to wire-format NDJSON events.

No retry, no masking, no transformation of upstream errors. The frontend
displays the raw ``status`` / ``code`` / ``message`` so users see exactly what
happened on the wire.
"""
from __future__ import annotations

from typing import Any

import aimux

from .llm import StartupConfigError


def to_wire(exc: BaseException) -> dict[str, Any]:
    """Return an NDJSON-ready dict representing this exception."""
    if isinstance(exc, StartupConfigError):
        return {
            "event": "error",
            "code": "StartupConfigError",
            "message": str(exc),
            "missing": list(exc.missing),
            "status": None,
            "body": None,
            "headers": None,
        }
    if isinstance(exc, aimux.AimuxError):
        return {
            "event": "error",
            "code": type(exc).__name__,
            "message": str(exc) or type(exc).__name__,
            # aimux exposes `status` / `response_body` on APICallError;
            # fall back to the old attribute names defensively.
            "status": getattr(exc, "status", None)
            or getattr(exc, "status_code", None),
            "body": getattr(exc, "response_body", None)
            or getattr(exc, "body", None),
            "headers": getattr(exc, "headers", None),
        }
    return {
        "event": "error",
        "code": type(exc).__name__,
        "message": str(exc) or repr(exc),
        "status": None,
        "body": None,
        "headers": None,
    }


__all__ = ["to_wire"]