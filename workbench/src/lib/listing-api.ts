import type { ProductCandidate } from "./selection-types";
import type {
  ListingIssue,
  ListingOutput,
  ListingWireEvent,
} from "./listing-types";

/**
 * Streaming client for the listing agent — mirrors `runSelection` exactly:
 * stream the body even on non-200 (wire error lines must reach the UI),
 * resolve with the final/error payload.
 */
export interface ListingRunPayload {
  candidate: ProductCandidate;
  marketCode?: string;
  brand?: string;
  competitorBrands?: string[];
  sessionId?: string;
}

export async function runListing(
  payload: ListingRunPayload,
  onEvent: (event: ListingWireEvent) => void,
  signal?: AbortSignal,
): Promise<{ final: ListingWireEvent | null }> {
  const res = await fetch("/api/listing/run", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      candidate: payload.candidate,
      marketCode: payload.marketCode ?? "US",
      brand: payload.brand ?? "",
      competitorBrands: payload.competitorBrands ?? [],
      // G1 ListingRunBody uses snake_case session_id. The interface
      // field stays camelCase (sessionId in ListingRunPayload) for clarity
      // at call sites; we only rename at the boundary to match the server.
      session_id: payload.sessionId,
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
  let lastFinal: ListingWireEvent | null = null;

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
          const ev = JSON.parse(line) as ListingWireEvent;
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

// ──── Dual-channel export / upload  ───────────────────────────────

export interface ListingFactsOverrides {
  price_usd?: number;
  quantity?: number;
  sku?: string;
  brand?: string;
}

export interface ListingExportResult {
  run_id: string | null;
  market_code: string;
  payload: { product_type: string; attributes: Record<string, unknown> };
  copy_blocks: Record<string, string>;
  facts: ListingFactsOverrides & { condition: string; fulfillment_channel: string };
}

/** 上传通道：api = 凭据齐全；export = 退化导出。 */
export async function fetchListingChannel(): Promise<"api" | "export"> {
  try {
    const res = await fetch("/api/listing/channel", { cache: "no-store" });
    const data = (await res.json()) as { channel?: string };
    return data.channel === "api" ? "api" : "export";
  } catch {
    return "export";
  }
}

function wireErrorMessage(data: unknown, fallback: string): string {
  const err = data as { message?: string };
  return err?.message ?? fallback;
}

export async function exportListing(
  runId: string,
  facts: ListingFactsOverrides,
): Promise<ListingExportResult> {
  const res = await fetch("/api/listing/export", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ run_id: runId, ...facts }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(wireErrorMessage(data, `Export failed: ${res.status}`));
  }
  return data as ListingExportResult;
}

export async function uploadListing(
  runId: string,
  facts: ListingFactsOverrides,
): Promise<{ status: number; body: unknown }> {
  const res = await fetch("/api/listing/upload", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ run_id: runId, ...facts }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(wireErrorMessage(data, `Upload failed: ${res.status}`));
  }
  return data as { status: number; body: unknown };
}

/** regenerate one field via the backend LLM. Returns the new value
 *  plus the re-run validation issues. Caller patches local state with
 *  `value` and replaces `issues`. Throws on wire error. */
export async function regenListingField(
  runId: string,
  fieldName: string,
): Promise<{
  field: string;
  value: string | string[];
  issues: ListingIssue[]; // narrowed server-side; cast on return
  hard_failed: boolean;
}> {
  const res = await fetch("/api/listing/regen-field", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ run_id: runId, field_name: fieldName }),
  });
  const data = (await res.json()) as
    | {
        field: string;
        value: string | string[];
        issues: ListingIssue[];
        hard_failed: boolean;
      }
    | { event: "error"; code: string; message: string; status?: number };
  if (!res.ok || (data as { event?: string }).event === "error") {
    const err = data as { code: string; message: string; status?: number };
    throw new Error(err.message ?? `Regen failed: ${res.status}`);
  }
  return data as {
    field: string;
    value: string | string[];
    issues: ListingIssue[];
    hard_failed: boolean;
  };
}

// ──── Persisted listing drafts ( Listing 列表) ──────────────────────

export interface ListingRunSummary {
  id: string;
  session_id: string;
  product_id: string;
  market_code: string;
  product_name: string;
  /** the Listing draft's real title
   *  (listing.item_name), surfaced so the card shows what the agent
   *  actually drafted rather than the candidate's marketing label. */
  listing_title: string;
  /** user-chosen alias (NULL when not
   *  renamed). Frontend renders this first if present. */
  title: string | null;
  hard_failed: boolean;
  created_at: string;
}

export interface ListingRunDetail {
  id: string;
  session_id: string;
  product_id: string;
  product_name: string;
  market_code: string;
  created_at: string;
  /** same dual-title surface as the list. */
  listing_title: string;
  title: string | null;
  /** Server envelope: {listing, issues, hard_failed, candidate?}. The
   *  candidate key is absent on legacy rows (added with the S4 CLI export
   *  work) — treat as optional. */
  listing: {
    listing: ListingOutput | null;
    issues: ListingIssue[] | null;
    hard_failed: boolean;
    candidate?: ProductCandidate;
  } | null;
}

/** Listing 列表 — summaries newest-first; network/wire errors → [] (view
 *  renders its empty state rather than a dead page). */
export async function fetchListingRuns(): Promise<ListingRunSummary[]> {
  try {
    const res = await fetch("/api/listing/runs", { cache: "no-store" });
    if (!res.ok) return [];
    const data = (await res.json()) as ListingRunSummary[] | { event?: string };
    if (Array.isArray(data)) return data;
    return [];
  } catch {
    return [];
  }
}

/** 只读详情 — null on 404 / wire error (the list row shows an inline error). */
export async function fetchListingRun(runId: string): Promise<ListingRunDetail | null> {
  try {
    const res = await fetch(`/api/listing/runs/${encodeURIComponent(runId)}`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as ListingRunDetail;
  } catch {
    return null;
  }
}

/** Rename a listing run (user-chosen alias).
 *  Returns the post-update summary. Throws on wire / network error. */
export async function renameListingRun(
  runId: string,
  title: string,
): Promise<ListingRunDetail> {
  const res = await fetch(`/api/listing/runs/${encodeURIComponent(runId)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ title }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(wireErrorMessage(data, `Rename failed: ${res.status}`));
  }
  return data as ListingRunDetail;
}

/** Hard-delete a listing run. Throws on
 *  wire / network error. The server returns 204 on success; we treat any
 *  2xx as success. */
export async function deleteListingRun(runId: string): Promise<void> {
  const res = await fetch(`/api/listing/runs/${encodeURIComponent(runId)}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) {
    let msg = `Delete failed: ${res.status}`;
    try {
      const data = await res.json();
      msg = wireErrorMessage(data, msg);
    } catch {
      // body may be empty on 5xx — keep the status-based fallback
    }
    throw new Error(msg);
  }
}

// ──── SP-API credentials (config page) ──────────────────────────────────────

export interface SpapiConfig {
  client_id?: string;
  client_secret?: string;
  refresh_token?: string;
  seller_id?: string;
  marketplace_ids?: string[];
  region?: string;
}

export async function fetchSpapiConfig(): Promise<SpapiConfig> {
  const res = await fetch("/api/selection/config/spapi", { cache: "no-store" });
  if (!res.ok) return {};
  return (await res.json()) as SpapiConfig;
}

export async function saveSpapiConfig(config: SpapiConfig): Promise<SpapiConfig> {
  const res = await fetch("/api/selection/config/spapi", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(config),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(wireErrorMessage(data, `Save failed: ${res.status}`));
  }
  return data as SpapiConfig;
}
