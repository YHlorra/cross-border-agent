
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Spawn the Python agent server (aiohttp) once per Node process and proxy
 * HTTP requests to it. The bridge listens on AGENT_PORT (default 8765).
 */

const AGENT_PORT = Number(process.env.AGENT_PORT ?? 8765);

// 仓库根定位：npm start/dev 都会先 `cd bff`（process.cwd=bff/），在 bff/ 下
// `python -m agent.server` 找不到 agent 包。以本文件位置为锚（bff/src → 上两级）
// 解析仓库根与 venv，spawn cwd 也用仓库根，与数据目录相对路径解析一致。
const REPO_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
);

// Bridge singletons live on globalThis so they survive Next dev HMR reloads
// (otherwise a recompile of this module resets child / readyPromise while
// the spawned Python agent is still bound to AGENT_PORT — the freshly loaded
// module then sees port-busy and refuses to re-adopt its own child).
type BridgeState = {
  child: ChildProcess | null;
  readyPromise: Promise<number> | null;
  stderrRing: string[];
  launchNonce: string;
};
const g = globalThis as unknown as { __agentBridgeState?: BridgeState };
const state: BridgeState =
  g.__agentBridgeState ??
  (g.__agentBridgeState = {
    child: null,
    readyPromise: null,
    stderrRing: [],
    launchNonce: process.env.AGENT_LAUNCH_NONCE || crypto.randomUUID(),
  });
const child = () => state.child;
const readyPromise = () => state.readyPromise;
const setChild = (c: ChildProcess | null) => {
  state.child = c;
};
const setReadyPromise = (p: Promise<number> | null): void => {
  state.readyPromise = p;
};
const stderrRing = () => state.stderrRing;
// A2b — random nonce per bridge lifetime. Passed to the agent as
// AGENT_LAUNCH_NONCE; the server's /healthz echoes it so a stale agent
// (orphan from a previous dev session) is rejected instead of adopted.
// Industry precedent: Terraform plugin magic cookie, Playwright launchServer
// wsEndpoint token.
const LAUNCH_NONCE = state.launchNonce;
// ring buffer of recent stderr lines (most-recent-last). Printed on
// non-zero child exit so the operator sees import / startup errors without
// needing AGENT_BRIDGE_DEBUG=1.
const STDERR_RING_MAX = 20;

function sleep(ms: number): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  setTimeout(resolve, ms);
  return promise;
}

/** Probe whether a TCP port is free on the loopback interface.
 *  Binds, then immediately closes. A TOCTOU window exists — the port can be
 *  taken between this probe and the agent's bind. Correctness of stale-server
 *  adoption is the nonce handshake's job (see A2b); this is fast-fail UX so the
 *  user sees an actionable message instead of waiting 10s for a timeout.
 */
function testPortFree(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const srv = createServer();
    srv.unref();
    srv.once("error", () => {
      srv.close(() => resolve(false));
    });
    srv.listen(port, "127.0.0.1", () => {
      srv.close(() => resolve(true));
    });
  });
}

/** Raised by ensureBridge when the agent port is already taken — typically a
 *  stale Python process from a previous dev session. Caught by proxyJson /
 *  proxyNdjson and converted to a 503 NDJSON wire response so the user sees
 *  the actionable error instead of a generic fetch failure.
 */
class AgentPortBusyError extends Error {
  constructor(port: number) {
    super(
      `agent port ${port} 已被占用（可能是残留 agent 进程），kill 后重试或设 AGENT_PORT 换端口`,
    );
    this.name = "AgentPortBusyError";
  }
}

async function waitForHealth(timeoutMs = 10000): Promise<number> {
  const deadline = Date.now() + timeoutMs;
  const url = `http://127.0.0.1:${AGENT_PORT}/healthz`;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { cache: "no-store" });
      // only 200 means ready. 503 (bad config) keeps polling — only
      // useful as a signal that the server is alive but unusable. Treat it
      // the same as a connection error so a stale but configured server
      // doesn't get adopted silently.
      if (res.status === 200) {
        // A2b — nonce handshake: stale servers (orphans from prior dev
        // sessions) return 200 too. The server now echoes the
        // AGENT_LAUNCH_NONCE we passed via env; mismatch → not ours.
        if (LAUNCH_NONCE) {
          const body = (await res.json().catch(() => null)) as
            | { nonce?: string }
            | null;
          if (body?.nonce !== LAUNCH_NONCE) {
            throw new Error(
              `检测到非本次启动的 agent（可能是残留进程），kill 后重试或设 AGENT_PORT 换端口`,
            );
          }
        }
        return res.status;
      }
    } catch (e) {
      // Distinguish nonce-mismatch (definitive failure) from transient
      // network errors (keep polling until deadline).
      if (e instanceof Error && e.message.includes("检测到非本次启动")) {
        throw e;
      }
    }
    await sleep(200);
  }
  throw new Error(`Agent bridge did not become ready within ${timeoutMs}ms`);
}

function waitForChildExit(proc: ChildProcess, timeoutMs: number): Promise<boolean> {
  if (proc.exitCode !== null || proc.signalCode !== null) return Promise.resolve(true);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (exited: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      proc.off("exit", onExit);
      proc.off("close", onClose);
      resolve(exited);
    };
    const onExit = () => finish(true);
    const onClose = () => finish(true);
    const timer = setTimeout(() => finish(false), timeoutMs);
    proc.once("exit", onExit);
    proc.once("close", onClose);
  });
}

async function terminateChild(proc: ChildProcess): Promise<void> {
  if (proc.exitCode !== null || proc.signalCode !== null) return;
  proc.stdin?.end();
  if (await waitForChildExit(proc, 2000)) return;
  try {
    proc.kill();
  } catch {
    return;
  }
  await waitForChildExit(proc, 2000);
}

function startChild(): ChildProcess {
  const cwd = REPO_ROOT;
  const venvPython = path.join(cwd, ".venv", "Scripts", "python.exe");
  const pythonExe = existsSync(venvPython) ? venvPython : "python";
  state.stderrRing = []; // reset for the new child
  const proc = spawn(pythonExe, ["-m", "agent.server"], {
    cwd,
    env: {
      ...process.env,
      AGENT_PORT: String(AGENT_PORT),
      AGENT_LAUNCH_NONCE: LAUNCH_NONCE,
    },
    // keep stdin as a pipe so the Python side can read from it in a
    // daemon thread; EOF (e.g. parent killed /F) triggers graceful
    // shutdown instead of leaving a dangling agent. stdout has no
    // consumer in HTTP mode, drop the pipe to avoid 64KB backpressure.
    stdio: ["pipe", "ignore", "pipe"],
    windowsHide: true,
  });
  proc.stderr?.on("data", (d) => {
    const text = d.toString();
    if (process.env.AGENT_BRIDGE_DEBUG === "1") {
      console.error("[agent-bridge stderr]", text);
    }
    // ring buffer keeps the last STDERR_RING_MAX lines so we can
    // dump them on non-zero exit without AGENT_BRIDGE_DEBUG.
    for (const line of text.split(/\r?\n/)) {
      if (!line) continue;
      state.stderrRing.push(line);
      if (state.stderrRing.length > STDERR_RING_MAX) state.stderrRing.shift();
    }
  });
  proc.on("exit", (code) => {
    if (code !== 0) {
      // surface the last few stderr lines so import / config
      // failures are visible without requiring AGENT_BRIDGE_DEBUG.
      const tail =
        state.stderrRing.length > 0
          ? state.stderrRing.join("\n")
          : "(no stderr captured)";
      console.error(
        `[agent-bridge] child exited with code ${code}; recent stderr:\n${tail}`,
      );
    } else if (process.env.AGENT_BRIDGE_DEBUG === "1") {
      console.error(`[agent-bridge] exited with code ${code}`);
    }
    if (child() === proc) {
      setChild(null);
      setReadyPromise(null);
    }
  });
  return proc;
}

export function ensureBridge(): Promise<number> {
  // Reuse a healthy existing child across Next dev HMR reloads (the module
  // is re-evaluated; child reference + readyPromise + nonce + stderr ring
  // all live on globalThis — see top of file).
  const existing = state.child;
  const existingPromise = state.readyPromise;
  if (existing && !existing.killed && existingPromise) {
    return existingPromise;
  }
  if (existing && !existing.killed) {
    // No readyPromise yet (the spawn promise itself was reset) but the
    // child is alive — just wait for it.
    return waitForHealth();
  }
  if (!existingPromise) {
    let attempt!: Promise<number>;
    attempt = (async () => {
      let launched: ChildProcess | null = null;
      try {
        if (!(await testPortFree(AGENT_PORT))) {
          throw new AgentPortBusyError(AGENT_PORT);
        }
        launched = startChild();
        setChild(launched);
        return await waitForHealth();
      } catch (e) {
        if (launched) await terminateChild(launched);
        if (child() === launched) setChild(null);
        if (readyPromise() === attempt) setReadyPromise(null);
        throw e;
      }
    })();
    setReadyPromise(attempt);
    return attempt;
  }
  return existingPromise;
}

export function agentBaseUrl(): string {
  return `http://127.0.0.1:${AGENT_PORT}`;
}

export async function proxyJson(
  pathSuffix: string,
  init?: RequestInit,
): Promise<Response> {
  try {
    await ensureBridge();
  } catch (e) {
    if (e instanceof AgentPortBusyError) {
      return agentUnavailableResponse(e.message);
    }
    throw e;
  }
  return fetch(`${agentBaseUrl()}${pathSuffix}`, init);
}

export async function proxyNdjson(
  pathSuffix: string,
  init: RequestInit,
): Promise<Response> {
  try {
    await ensureBridge();
  } catch (e) {
    if (e instanceof AgentPortBusyError) {
      return agentUnavailableResponse(e.message);
    }
    throw e;
  }
  const upstream = await fetch(`${agentBaseUrl()}${pathSuffix}`, init);
  // Re-stream the body back to the client. Web fetch supports ReadableStream
  // directly in Response constructor.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: {
      "Content-Type":
        upstream.headers.get("Content-Type") ?? "application/x-ndjson",
      "Cache-Control": "no-cache",
    },
  });
}

function agentUnavailableResponse(message: string): Response {
  const body =
    JSON.stringify({
      event: "error",
      code: "AgentUnavailable",
      message,
      status: 503,
    }) + "\n";
  return new Response(body, {
    status: 503,
    headers: { "Content-Type": "application/x-ndjson" },
  });
}