import type { ChatWireEvent } from "./chat-types";

/**
 * Streaming client for the general-agent chat channel (general-agent-chat).
 * Mirrors runSelection: POSTs to `/api/chat/run` (Next proxy → aiohttp
 * bridge `/chat`) and invokes `onEvent` per NDJSON frame. Error responses
 * carry wire-format NDJSON lines that must reach the UI verbatim.
 */
export async function runChat(
  query: string,
  opts: { sessionId?: string },
  onEvent: (event: ChatWireEvent) => void,
  signal?: AbortSignal,
): Promise<{ final: ChatWireEvent | null }> {
  const res = await fetch("/api/chat/run", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      query,
      session_id: opts.sessionId,
    }),
    signal,
  });

  if (!res.body) {
    onEvent({
      event: "error",
      code: "NetworkError",
      message: `Request failed: ${res.status} ${res.statusText}`,
      status: res.status,
    });
    return { final: null };
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastFinal: ChatWireEvent | null = null;

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let nl: number;
      while ((nl = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        try {
          const ev = JSON.parse(line) as ChatWireEvent;
          onEvent(ev);
          if (ev.event === "final" || ev.event === "error") {
            lastFinal = ev;
          }
        } catch (e) {
          onEvent({
            event: "error",
            code: "JSONParseError",
            message: `Failed to parse event: ${(e as Error).message} | line=${line.slice(0, 80)}`,
          });
        }
      }
    }
  } finally {
    reader.releaseLock();
  }

  if (!res.ok && !lastFinal) {
    onEvent({
      event: "error",
      code: "NetworkError",
      message: `Request failed: ${res.status} ${res.statusText}`,
      status: res.status,
    });
    return { final: null };
  }

  return { final: lastFinal };
}

export interface CompactOutcome {
  ok: boolean;
  /** compaction_end detail: result summary / aborted / errorMessage. */
  detail: string;
}

/**
 * Manual session compaction (slash /compact,  Addendum 5). POSTs to
 * `/api/selection/compact` (NDJSON: compaction_start → compaction_end) and
 * resolves with the terminal outcome; wire/network errors fold into
 * `{ok:false, detail}` so the caller can surface one system message.
 */
export async function compactSession(sessionId: string): Promise<CompactOutcome> {
  try {
    const res = await fetch("/api/selection/compact", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (!res.ok) {
      return { ok: false, detail: `压缩请求失败：HTTP ${res.status}` };
    }
    const text = await res.text();
    let detail = "";
    let aborted = false;
    for (const line of text.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const frame = JSON.parse(trimmed) as {
          event?: string;
          errorMessage?: string;
          aborted?: boolean;
          result?: unknown;
        };
        if (frame.event === "compaction_end") {
          aborted = Boolean(frame.aborted);
          detail =
            frame.errorMessage ||
            (typeof frame.result === "string" ? frame.result : "") ||
            (aborted ? "已中止" : "");
        }
      } catch {
        // non-JSON line — ignore (throat never emits them; defensive only)
      }
    }
    return { ok: !aborted && detail === "" ? true : !aborted, detail };
  } catch (e) {
    return { ok: false, detail: e instanceof Error ? e.message : String(e) };
  }
}
