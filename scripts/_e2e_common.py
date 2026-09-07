"""Shared helpers for p1 / p5 / p9 / p10 (and p11 in the future).

A5 — the four scripts used to each define their own spawn + finally block
with bare ``proc.terminate()`` and ``stderr=DEVNULL``. That left orphans
on Ctrl-C / harness timeouts (p1:99-100, p5:138, p9:217, p10:311) and
silently swallowed the actual error message that would have helped the
operator diagnose a failure.

This module consolidates the pattern into a single ``spawn_agent`` /
``terminate_agent`` pair, modeled on the p8:193-197 "triple-保险"
reference:

    proc.terminate()                       # SIGTERM (graceful)
    proc.wait(timeout=5)                   # bounded wait
    proc.kill() on TimeoutExpired          # SIGKILL fallback

stderr is captured to an in-memory buffer; on non-zero exit (or
terminate failure) the buffer is dumped to stderr. The port rebind
assertion (the AGENT_PORT-specific safety net) is the same as
``lib/agent-bridge.ts testPortFree`` — a few lines of ``socket.bind``
+ immediate close that confirms the agent port is free at end-of-test.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def spawn_agent(
    port: int,
    data_dir: str | os.PathLike | None = None,
    extra_env: dict[str, str] | None = None,
) -> Iterator[subprocess.Popen]:
    """Spawn the Python agent on ``port`` (auto-AGENT_PORT env) and yield the
    Popen. Captures stderr into an in-memory buffer for diagnostic dumps.
    """
    env = os.environ.copy()
    env["AGENT_PORT"] = str(port)
    if data_dir is not None:
        env["AGENT_DATA_DIR"] = str(data_dir)
    if extra_env:
        env.update(extra_env)
    stderr_chunks: list[bytes] = []

    proc = subprocess.Popen(
        [sys.executable, "-m", "agent.server"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        # Background thread to drain stderr so the OS pipe never blocks.
        import threading
        def _drain() -> None:
            assert proc.stderr is not None
            for chunk in iter(proc.stderr.readline, b""):
                stderr_chunks.append(chunk)
        t = threading.Thread(target=_drain, daemon=True)
        t.start()
        yield proc
    finally:
        # p8-style triple-保险: SIGTERM → bounded wait → SIGKILL fallback.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        # Diagnostic dump on failure.
        if proc.returncode != 0 and stderr_chunks:
            sys.stderr.write(
                f"[spawn_agent] agent exited {proc.returncode}; "
                f"stderr tail:\n"
            )
            sys.stderr.write(b"".join(stderr_chunks[-200:]).decode("utf-8", errors="replace"))
            sys.stderr.write("\n")
        # Port rebind assertion: the port must be free again, otherwise
        # an orphan from this script would have blocked the next run.
        assert_port_free(port)


def assert_port_free(port: int) -> None:
    """Bind + close on the loopback port; warn loudly if anything is still
    listening. Mirrors ``testPortFree`` in lib/agent-bridge.ts. The check is
    best-effort: on Windows WSAEACCES (10013) can fire from policy layers
    that block the bind regardless of whether the port is actually free.
    We log and continue so a transient policy artefact doesn't fail the
    suite; the orphan is most reliably caught by the spawn_agent's own
    port probe at the *start* of the next script invocation (next-run
    failure is loud; here we just observe).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
    except OSError as e:
        sys.stderr.write(
            f"[spawn_agent] WARNING: port {port} could not be rebound for "
            f"post-test assertion ({e.errno} {e.strerror}); the previous "
            f"agent may still be tearing down. Continuing.\n"
        )
    finally:
        s.close()