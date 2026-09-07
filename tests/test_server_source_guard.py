"""SEAM — local_source_guard middleware (Host/Origin pinning).

No network: the aiohttp test client drives the real build_app() routes and
asserts the guard's allow/deny matrix — loopback Host (any port) passes,
foreign Host / foreign Origin / literal "null" Origin are 403
ForbiddenSource, and Origin-less requests (agent-bridge / curl paths) plus
the dev frontend's http://localhost:3000 Origin are admitted.
"""
from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import agent.server as srv


@pytest.fixture
def app() -> web.Application:
    return srv.build_app()


async def _scenario(app: web.Application, headers: dict | None = None) -> tuple[int, str, dict]:
    """GET /config through the real app; the response is fully consumed
    inside the client's lifetime (an aiohttp response cannot outlive its
    TestClient context)."""
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/config", headers=headers or {})
        status = resp.status
        content_type = resp.content_type
        body = json_loads(await resp.text())
        return status, content_type, body


def json_loads(text: str) -> dict:
    import json

    return json.loads(text)


def _run(coro):
    return asyncio.run(coro)


def test_loopback_host_passes(app) -> None:
    """The bridge/Node fetch path: loopback Host, no Origin header."""
    status, _, _ = _run(_scenario(app, {"Host": "127.0.0.1:8765"}))
    assert status == 200


def test_foreign_host_rejected(app) -> None:
    status, content_type, body = _run(_scenario(app, {"Host": "evil.com"}))
    assert status == 403
    assert content_type == "application/json"
    assert body["code"] == "ForbiddenSource" and body["status"] == 403


def test_foreign_origin_rejected(app) -> None:
    """A real browser sends Host: loopback but Origin: attacker site — the
    cross-site simple-request vector."""
    status, _, body = _run(
        _scenario(app, {"Host": "127.0.0.1:8765", "Origin": "http://evil.com"})
    )
    assert status == 403
    assert body["code"] == "ForbiddenSource"


def test_null_origin_rejected(app) -> None:
    """Sandboxed iframe's literal Origin: null must fail closed."""
    status, _, body = _run(_scenario(app, {"Host": "127.0.0.1:8765", "Origin": "null"}))
    assert status == 403
    assert body["code"] == "ForbiddenSource"


def test_localhost_origin_passes(app) -> None:
    """The dev frontend path: Origin http://localhost:3000 (port ignored)."""
    status, _, _ = _run(
        _scenario(app, {"Host": "127.0.0.1:8765", "Origin": "http://localhost:3000"})
    )
    assert status == 200


def test_ipv6_host_passes(app) -> None:
    status, _, _ = _run(_scenario(app, {"Host": "[::1]:8765"}))
    assert status == 200


def test_default_testclient_host_passes(app) -> None:
    """No explicit headers: the client's default Host (127.0.0.1:<port>) is
    the Origin-less curl/bridge shape."""
    status, _, _ = _run(_scenario(app))
    assert status == 200


def test_hostname_parsing_units() -> None:
    """Unit pins for the authority parsers (IPv6 bracket + bare, ports)."""
    assert srv._hostname_of("127.0.0.1:8765") == "127.0.0.1"
    assert srv._hostname_of("localhost") == "localhost"
    assert srv._hostname_of("[::1]:8765") == "::1"
    assert srv._hostname_of("::1") == "::1"
    assert srv._hostname_of("evil.com:80") == "evil.com"
    assert srv._origin_hostname("http://localhost:3000") == "localhost"
    assert srv._origin_hostname("https://127.0.0.1") == "127.0.0.1"
    assert srv._origin_hostname("null") is None
    assert srv._origin_hostname("ftp://localhost") is None
    assert srv._origin_hostname("") is None
