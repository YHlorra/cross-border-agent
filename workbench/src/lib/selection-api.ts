import type {
  HistoryEntry,
  HistoryRunDetail,
  RunOptions,
  SessionEvent,
  SessionListItem,
  WireEvent,
} from "./selection-types";
import type {
  ConfigSnapshot,
  ConfigSaveResult,
  ModelConfig,
  ModelOption,
  ProviderOption,
  ProviderRecord,
  SavedProvider,
  WireError,
} from "./config-types";
import { ConfigApiError } from "./config-types";

/**
 * Streaming client for the selection agent.
 *
 * `runSelection` POSTs to `/api/selection/run` (which proxies to the
 * aiohttp bridge) and invokes `onEvent` for each NDJSON frame. The promise
 * resolves when the `final` or `error` event arrives, with the final payload.
 */
export async function runSelection(
  query: string,
  opts: RunOptions,
  onEvent: (event: WireEvent) => void,
  signal?: AbortSignal,
): Promise<{ final: WireEvent | null }> {
  const res = await fetch("/api/selection/run", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      query,
      // Backend reads ``session_id`` (snake_case) — fix the long-standing
      // mismatch that left every run with a fresh UUID.
      session_id: opts.sessionId,
      historyRunId: opts.historyRunId,
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

  // Stream the body even on non-200: error responses (400/503) carry
  // wire-format NDJSON lines (e.g. StartupConfigError with its missing list)
  // that must reach the UI verbatim — discarding them would mask the real
  // failure behind a generic NetworkError.
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastFinal: WireEvent | null = null;

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Split on newline; trailing partial line stays in buffer for next chunk.
      let nl: number;
      while ((nl = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        try {
          const ev = JSON.parse(line) as WireEvent;
          onEvent(ev);
          if (ev.event === "final" || ev.event === "error") {
            lastFinal = ev;
          }
        } catch (e) {
          // Malformed JSON; surface as error so UI can show.
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
    // Non-OK response whose body yielded no wire error event — only then
    // fall back to a generic network error.
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

export async function fetchHistory(): Promise<HistoryEntry[]> {
  try {
    const res = await fetch("/api/selection/history", { cache: "no-store" });
    if (!res.ok) return [];
    return (await res.json()) as HistoryEntry[];
  } catch {
    return [];
  }
}

/** fetch one persisted run's full envelope for replay / open-history. */
export async function fetchHistoryRun(runId: string): Promise<HistoryRunDetail | null> {
  try {
    const res = await fetch(
      `/api/selection/history/${encodeURIComponent(runId)}`,
      { cache: "no-store" },
    );
    if (res.status === 404) return null;
    if (!res.ok) return null;
    return (await res.json()) as HistoryRunDetail;
  } catch {
    return null;
  }
}

/** list all sessions aggregated from messages. */
export async function fetchSessions(): Promise<SessionListItem[]> {
  try {
    const res = await fetch(
      `/api/selection/sessions`,
      { cache: "no-store" },
    );
    if (!res.ok) return [];
    return (await res.json()) as SessionListItem[];
  } catch {
    return [];
  }
}

/**  addendum — PATCH /sessions/{id} (sidebar context-menu actions:
 *  pin / rename / mark unread). Partial update; unknown keys are
 *  silently dropped server-side per the ADR contract. Returns the post-update
 *  meta row. */
export async function updateSessionMeta(
  sessionId: string,
  fields: {
    title?: string;
    pinned?: boolean;
    last_read_at?: string;
  },
): Promise<{ ok: boolean }> {
  try {
    const res = await fetch(
      `/api/selection/sessions/${encodeURIComponent(sessionId)}`,
      {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(fields),
      },
    );
    return { ok: res.ok };
  } catch {
    return { ok: false };
  }
}

/** List events of a session in sequence order.
 *  Default ``includeFailed=false`` excludes error-run events; pass ``true``
 *  for audit views. */
/**
 *  On failure this returns an explicit ``error`` string instead of silently
 *  returning ``[]`` (which the UI would render as "打开会话失败（无事件）").
 */
export interface FetchSessionEventsResult {
  events: SessionEvent[];
  error?: string;
}

export async function fetchSessionEvents(
  sessionId: string,
  options: { includeFailed?: boolean } = {},
): Promise<FetchSessionEventsResult> {
  const qs = options.includeFailed ? "?include_failed=true" : "";
  try {
    const res = await fetch(
      `/api/selection/sessions/${encodeURIComponent(sessionId)}/events${qs}`,
      { cache: "no-store" },
    );
    if (!res.ok) {
      return {
        events: [],
        error: `server returned ${res.status} ${res.statusText}`,
      };
    }
    return { events: (await res.json()) as SessionEvent[] };
  } catch (e) {
    return {
      events: [],
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

/** wipe all messages of a session. Wired to 记忆清除开关. */
export async function deleteSession(sessionId: string): Promise<boolean> {
  try {
    const res = await fetch(
      `/api/selection/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
    );
    return res.ok || res.status === 204;
  } catch {
    return false;
  }
}

/** POST /compact via the proxy; consumes the NDJSON stream and
 *  returns the final compaction_end event. Throws on stream-level errors. */
export async function compactSession(
  sessionId: string,
  options: { customInstructions?: string } = {},
): Promise<import("./selection-types").CompactionEndEvent> {
  const res = await fetch("/api/selection/compact", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      custom_instructions: options.customInstructions,
    }),
  });
  if (!res.body) {
    throw new Error("compact: empty body");
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let endEvent: import("./selection-types").CompactionEndEvent | null = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf("\n")) !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      try {
        const ev = JSON.parse(line);
        if (ev.event === "compaction_end") {
          endEvent = ev as import("./selection-types").CompactionEndEvent;
        }
      } catch {
        // ignore malformed
      }
    }
  }
  if (!endEvent) {
    throw new Error("compact: stream closed without compaction_end event");
  }
  return endEvent;
}

export async function fetchConfig(): Promise<ConfigSnapshot | null> {
  try {
    const res = await fetch("/api/selection/config", { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as ConfigSnapshot;
  } catch {
    return null;
  }
}

// ──── LLM config page ────────────────────────────────────────────────────────

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = (await res.json()) as T | WireError;
  if (!res.ok || (data as WireError).event === "error") {
    const err = data as WireError;
    throw new ConfigApiError(err.message, err.code, err.status ?? null);
  }
  return data as T;
}

export async function fetchProviders(): Promise<ProviderOption[]> {
  const res = await postJson<{ providers: ProviderOption[] }>(
    "/api/selection/providers",
    {},
  );
  return res.providers;
}

export async function fetchModels(
  provider: string,
  apiKey: string,
): Promise<ModelOption[]> {
  const res = await postJson<{ models: ModelOption[] }>(
    "/api/selection/models",
    { provider, api_key: apiKey },
  );
  return res.models;
}

export async function testConnection(
  provider: string,
  apiKey: string,
  model: string,
  baseUrl?: string,
): Promise<{ ok: boolean; reply: string }> {
  return postJson<{ ok: boolean; reply: string }>("/api/selection/test", {
    provider,
    api_key: apiKey,
    model,
    base_url: baseUrl,
  });
}

export async function saveLlmConfig(payload: {
  api_key: string;
  primary_provider: string;
  primary_model: string;
  cheap_provider: string;
  cheap_model: string;
  base_url?: string;
}): Promise<ConfigSaveResult> {
  return postJson<ConfigSaveResult>("/api/selection/config/save", payload);
}

export async function fetchSavedProviders(): Promise<SavedProvider[]> {
  const res = await fetch("/api/selection/config/saved", {
    method: "GET",
    cache: "no-store",
  });
  const data = (await res.json()) as { saved?: SavedProvider[] } | WireError;
  if (!res.ok || (data as WireError).event === "error") {
    const err = data as WireError;
    throw new ConfigApiError(err.message, err.code, err.status ?? null);
  }
  return (data as { saved?: SavedProvider[] }).saved ?? [];
}

export async function saveProvider(
  record: ProviderRecord,
): Promise<SavedProvider> {
  return postJson<SavedProvider>("/api/selection/config/saved", record);
}

export async function deleteSavedProvider(name: string): Promise<void> {
  await postJson<{ deleted: string }>(
    "/api/selection/config/saved/delete",
    { name },
  );
}

export async function fetchModelConfig(): Promise<ModelConfig> {
  const res = await fetch("/api/selection/config/model", {
    method: "GET",
    cache: "no-store",
  });
  const data = (await res.json()) as ModelConfig | WireError;
  if (!res.ok || (data as WireError).event === "error") {
    const err = data as WireError;
    throw new ConfigApiError(err.message, err.code, err.status ?? null);
  }
  return data as ModelConfig;
}

export async function saveModelConfig(
  config: ModelConfig,
): Promise<ModelConfig> {
  return postJson<ModelConfig>("/api/selection/config/model", config);
}

// Re-export types consumed by components from the shared module.
export type {
  ConfigSnapshot,
  ConfigSaveResult,
  ModelConfig,
  ModelOption,
  ProviderOption,
  ProviderRecord,
  SavedProvider,
};
