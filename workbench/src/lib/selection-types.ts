/**
 * Selection agent types — mirror src/agent/state.py (Pydantic) field-for-field.
 * Keep the two in lock-step when adding / renaming fields.
 */

export type DimensionKey =
  | "market_demand"
  | "competition"
  | "profit"
  | "seasonality"
  | "repurchase";

export interface DimensionScore {
  dimension: DimensionKey;
  score: number; // 1-10
  reason: string;
}

export interface ProductCandidate {
  product_id: string;
  name_cn: string;
  name_en: string;
  source_price_cny: number;
  target_price_usd: number;
  price_gap_ratio: number;
  category: string;
  bsr_rank?: number | null;
  bsr_top_percent?: number | null;
  review_count: number;
  monthly_search?: number | null;
  trend_growth?: number | null;
  trend_series?: number[] | null;
  weight_g?: number | null;
  image_url?: string | null;
}

export interface ScoredCandidate extends ProductCandidate {
  scores: DimensionScore[];
  total_score: number;
  recommendation: "go" | "caution" | "no-go";
  opportunities: string[];
  risks: string[];
}

export interface DetailedAnalysis {
  product_id: string;
  opportunities: string[];
  risks: string[];
  cut_in_direction: string;
}

export interface SelectionReport {
  session_id: string;
  seed_keyword: string;
  market_summary: string;
  top_recommendations: DetailedAnalysis[];
  created_at: string;
}

export type Decision = "go" | "caution" | "no-go" | "no_result";

// ──── NDJSON events ──────────────────────────────────────────────────────────

export type WireEvent =
  | { event: "node_start"; node: string }
  | {
      event: "tool_call";
      tool: string;
      args: Record<string, unknown>;
    }
  | {
      event: "node_end";
      node: string;
      summary: string;
      duration_ms: number;
      /** Legacy graph runs only (replay) — the parsed intent, for the
       *  "理解需求" card. Loop runs never emit node_end. */
      intent?: {
        seed_keyword: string;
        category_hint?: string | null;
        budget_cny?: number | null;
        preferences?: string[];
        confidence?: string;
      };
    }
  | { event: "report_chunk"; delta: string }
  // create_agent loop events (wire only adds).
  | { event: "turn_start"; turn: number; tool_calls_used: number }
  | {
      event: "tool_result";
      tool: string;
      ok: boolean;
      duration_ms: number;
      summary?: string;
      error?: string | null;
    }
  | { event: "turn_end"; turn: number; tool_calls?: boolean; error?: boolean }
  | { event: "agent_delta"; delta: string; turn?: number }
  | {
      event: "final";
      decision: Decision;
      report: SelectionReport | null;
      candidates: ScoredCandidate[];
      market_summary: string;
      session_id: string;
      /** Server-generated per-invocation run id (final envelope). */
      run_id?: string;
    }
  | { event: "empty"; keyword: string; message: string }
  | {
      event: "error";
      code: string;
      message: string;
      status?: number | null;
      body?: unknown;
      headers?: unknown;
      missing?: string[];
    }
  /** session-level metadata; no per-run state change (applyEvent
   *  treats it as a no-op; the summary renders separately on the timeline). */
  | {
      event: "compaction";
      summary: string;
      first_kept_event_index: number;
      tokens_before: number;
      details?: {
        retained_tail_count?: number;
        summarized_count?: number;
        is_incremental?: boolean;
      };
      reason: "manual" | "threshold" | "overflow";
    };

// ──── History entry ─────────────────────────────────────────────────────────

export interface HistoryTopSummary {
  top_name: string | null;
  top_score: number | null;
}

export interface HistoryEntry {
  id: string;
  query: string;
  seed_keyword: string;
  decision: Decision | "";
  /** Top candidate summary (null for legacy runs saved without candidates). */
  top?: HistoryTopSummary | null;
  /** Persisted intent for form backfill on replay (null on legacy rows). */
  intent?: {
    budget_cny?: number | null;
    preferences?: string[];
  } | null;
  created_at: string;
}

/** Full envelope returned by GET /history/{run_id} (frontend replay).
 *  Mirrors ``persistence.get_run`` field-for-field — keep the two in sync
 *  when extending.
 */
export interface HistoryRunDetail {
  id: string;
  query: string;
  seed_keyword: string;
  decision: Decision | "";
  report: SelectionReport | null;
  candidates: ScoredCandidate[];
  intent: {
    seed_keyword: string;
    category_hint?: string | null;
    budget_cny?: number | null;
    preferences?: string[];
    confidence?: string;
    rationale?: string;
  } | null;
  created_at: string;
}

// ──── Run options ───────────────────────────────────────────────────────────

export interface RunOptions {
  /** Backend field is ``session_id`` (snake_case).
   *  frontend↔backend field-name mismatch — see selection-api.ts. */
  sessionId?: string;
  /** Follow-up channel: render this run's envelope into intent context.
   *  Bad id → backend 404 (honest failure, no silent degrade). */
  historyRunId?: string;
}

// ──── Sessions  ─────────────────────────────────────────────────

/** One row from ``GET /sessions`` — sidebar session-list source of truth. */
export interface SessionListItem {
  session_id: string;
  first_event_at: string;
  last_event_at: string;
  event_count: number;
  run_count: number;
  has_failed: boolean;
  /**  addendum — sidebar context menu metadata. Defaults when the
   *  session has no session_meta row yet. ``unread = last_event_at >
   *  last_read_at`` is computed client-side from these two timestamps. */
  title: string | null;
  pinned: boolean;
  last_read_at: string | null;
}

/** One event row from ``GET /sessions/{id}/events``. ``payload`` is the full
 *  wire event JSON (``event`` field repeated inside payload for symmetry). */
export interface SessionEvent {
  id: string;
  session_id: string;
  run_id: string;
  event_type:
    | "user_message"
    | "node_start"
    | "node_end"
    | "tool_call"
    | "report_chunk"
    | "final"
    | "empty"
    | "error"
    | "compaction"
    | string;
  sequence: number;
  payload: Record<string, unknown> | null;
  answer_failed: boolean;
  created_at: string;
}

// ──── Compaction  ───────────────────────────────────────────────

export interface CompactionStartEvent {
  event: "compaction_start";
  reason: "manual" | "threshold" | "overflow";
}

export interface CompactionEndResult {
  summary: string;
  first_kept_event_index: number;
  tokens_before: number;
  estimated_tokens_after: number;
}

export interface CompactionEndEvent {
  event: "compaction_end";
  reason: "manual" | "threshold" | "overflow";
  result: CompactionEndResult | null;
  aborted: boolean;
  willRetry: boolean;
  errorMessage?: string;
}

// ──── Constants ─────────────────────────────────────────────────────────────

export const DIMENSIONS: { key: DimensionKey; label: string; weight: number }[] = [
  { key: "market_demand", label: "市场需求", weight: 0.25 },
  { key: "competition", label: "竞争度", weight: 0.2 },
  { key: "profit", label: "利润空间", weight: 0.3 },
  { key: "seasonality", label: "季节性", weight: 0.1 },
  { key: "repurchase", label: "复购率", weight: 0.15 },
];