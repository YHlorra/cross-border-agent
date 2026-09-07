/**
 * Run state model for the chat workbench — one agent message wraps the full
 * lifecycle of a single selection run (NDJSON events folded in by applyEvent).
 * Pure logic, no JSX: imported by workbench (dispatch) and message components.
 */

import type {
  HistoryRunDetail,
  ScoredCandidate,
  SelectionReport,
  SessionEvent,
  WireEvent,
} from "@/lib/selection-types";
import type { ListingWireEvent } from "@/lib/listing-types";
import type { ChatWireEvent } from "@/lib/chat-types";
import type { ProductCandidate } from "@/lib/selection-types";
import {
  applyListingEvent,
  newListingRun,
  type ListingRunState,
} from "./listing-run-state";
import {
  applyChatEvent,
  chatRunFromReplay,
  newChatRun,
  type ChatRunState,
} from "./chat-run-state";
import type { AgentTurn, ToolTrace, TurnToolCall } from "./turn-types";

// general-agent-chat 2.4 — the turn/tool types moved to turn-types.ts (shared
// with chat-run-state without a circular import); re-exported for compat.
export type { AgentTurn, ToolTrace, TurnToolCall };

/** Legacy graph-run flat tool event (loop runs use TurnToolCall in turns). */
export interface ToolCallEvent {
  tool: string;
  args: Record<string, unknown>;
  at: number;
}

export interface PipelineNode {
  id: string;
  label: string;
  detail: string;
  status: "pending" | "running" | "done";
}

/** display nodes for a run: the five-step DAG (legacy graph runs) or a
 *  single macro "选品 Agent" card (loop runs). */
export function displayNodes(run: RunState): PipelineNode[] {
  if (run.agentSkill) {
    return [
      {
        id: "agent",
        label: "选品 Agent",
        detail: run.agentSkill.detail,
        status: run.agentSkill.status,
      },
    ];
  }
  return run.nodes;
}

/** flat tool trace for the UI (loop runs: turns flatten; graph runs:
 *  the plain list). ui-refresh loop runs that hit a terminal phase
 *  get dangling calls synthesized as interrupted so the timeline never
 *  shows a fake running row after cancel/failure (read-side derivation;
 *  stored state is untouched). */
export function toolTrace(run: RunState): ToolTrace[] {
  const terminal = run.phase === "cancelled" || run.phase === "error";
  const synth = (t: ToolTrace): ToolTrace =>
    run.agentSkill && terminal && t.ok === undefined
      ? { ...t, ok: false, interrupted: true }
      : t;
  if (run.agentSkill) {
    return run.turns.flatMap((t) => t.toolCalls.map(synth));
  }
  return run.toolCalls.map((t) => ({ tool: t.tool, args: t.args }));
}

export interface IntentSummary {
  seed_keyword: string;
  category_hint?: string | null;
  budget_cny?: number | null;
  preferences?: string[];
  confidence?: string;
}

export interface ErrorInfo {
  code: string;
  message: string;
  status?: number | null;
  missing?: string[];
}

export type RunPhase =
  | "running"
  | "final"
  | "empty"
  | "error"
  | "cancelled";

export interface RunState {
  query: string;
  phase: RunPhase;
  startedAt: number;
  nodes: PipelineNode[];
  toolCalls: ToolCallEvent[];
  streamedReport: string;
  intent: IntentSummary | null;
  report: SelectionReport | null;
  candidates: ScoredCandidate[];
  decision: string;
  errorInfo: ErrorInfo | null;
  /** empty event payload — the honest no-result card. */
  emptyKeyword: string;
  emptyMessage: string;
  /** loop-run macro card (null for legacy graph runs). */
  agentSkill: { status: "running" | "done"; detail: string } | null;
  /** loop-run dynamic turns ([] for legacy graph runs). */
  turns: AgentTurn[];
  /** loop-run streamed final answer (agent_delta accumulation). */
  agentAnswer: string;
}

/** single macro "选品 agent" skill card for loop runs. */
export const AGENT_SKILL_NODE: PipelineNode = {
  id: "agent",
  label: "选品 Agent",
  detail: "等待开始",
  status: "pending",
};

export const PIPELINE_NODES: PipelineNode[] = [
  { id: "intent", label: "理解需求", detail: "等待开始", status: "pending" },
  { id: "retrieve", label: "数据检索", detail: "等待开始", status: "pending" },
  { id: "hot_filter", label: "热度筛选", detail: "等待开始", status: "pending" },
  { id: "quality_score", label: "五维评分", detail: "等待开始", status: "pending" },
  { id: "report", label: "生成报告", detail: "等待开始", status: "pending" },
];

export function newRun(query: string): RunState {
  return {
    query,
    phase: "running",
    startedAt: Date.now(),
    nodes: PIPELINE_NODES.map((n) => ({ ...n })),
    toolCalls: [],
    streamedReport: "",
    intent: null,
    report: null,
    candidates: [],
    decision: "",
    errorInfo: null,
    emptyKeyword: "",
    emptyMessage: "",
    agentSkill: null,
    turns: [],
    agentAnswer: "",
  };
}

/** open a historical run in the timeline without re-running the LLM.
 *  All nodes are pre-marked done; the result card renders immediately. */
export function runFromHistory(detail: HistoryRunDetail): RunState {
  const startedAt = Date.parse(detail.created_at) || Date.now();
  const intent = detail.intent;
  return {
    query: detail.query,
    phase: "final",
    startedAt,
    nodes: PIPELINE_NODES.map((n) => ({
      ...n,
      status: "done",
      detail: "已完成 · 持久化",
    })),
    toolCalls: [],
    streamedReport: "",
    intent: intent
      ? {
          seed_keyword: intent.seed_keyword,
          category_hint: intent.category_hint ?? null,
          budget_cny: intent.budget_cny ?? null,
          preferences: intent.preferences ?? [],
          confidence: intent.confidence ?? "low",
        }
      : null,
    report: detail.report,
    candidates: detail.candidates,
    decision: detail.decision,
    errorInfo: null,
    emptyKeyword: "",
    emptyMessage: "",
    agentSkill: null,
    turns: [],
    agentAnswer: "",
  };
}

/** split session events into one (user_message + wire events)
 *  tuple per run. Wire events are exactly the NDJSON frames the agent emitted
 *  live; the ``user_message`` event is a replay contract that records
 *  the original prompt + form signals (sequence=0) for each invocation.
 *  Replay uses applyEvent for the wire events and skips user_message rows.
 *
 *  Multi-run grouping. Previously this returned a single
 *  ``userMessage + wireEvents`` pair which forced ``replaySessionEvents``
 *  to fold all of a session's runs into one RunState (the multi-run fold
 *  bug — opening a 2-run session displayed only the first turn's query and
 *  a tangled run fold). With server-side session aggregation fixed
*  now actually creates multi-run sessions), the replay must match: one
*  pair per run, ordered by each run's first event time.
*/
export interface RunReplay {
  runId: string;
  /** ``null`` if this run has no user_message row (legacy / edge cases). */
  userMessage: Record<string, unknown> | null;
  /** Wire events (user_message filtered out), sorted by sequence ascending. */
  wireEvents: SessionEvent[];
}

function compareCreatedAt(a: SessionEvent, b: SessionEvent): number {
  const at = Date.parse(a.created_at);
  const bt = Date.parse(b.created_at);
  if (!Number.isNaN(at) && !Number.isNaN(bt) && at !== bt) return at - bt;
  if (!Number.isNaN(at) && Number.isNaN(bt)) return -1;
  if (Number.isNaN(at) && !Number.isNaN(bt)) return 1;
  return a.created_at.localeCompare(b.created_at);
}

function compareWithinRun(a: SessionEvent, b: SessionEvent): number {
  return (
    a.sequence - b.sequence ||
    compareCreatedAt(a, b) ||
    a.id.localeCompare(b.id)
  );
}

function compareRunStart(a: SessionEvent, b: SessionEvent): number {
  return compareCreatedAt(a, b) || a.sequence - b.sequence || a.id.localeCompare(b.id);
}

type RunGroup = {
  runId: string;
  userMessage: Record<string, unknown> | null;
  wireEvents: SessionEvent[];
  firstEvent: SessionEvent;
};

export function splitSessionEvents(events: SessionEvent[]): RunReplay[] {
  const groups = new Map<string, RunGroup>();
  for (const ev of events) {
    const g = groups.get(ev.run_id);
    if (!g) {
      groups.set(ev.run_id, {
        runId: ev.run_id,
        userMessage: ev.event_type === "user_message" ? ev.payload ?? null : null,
        wireEvents: ev.event_type === "user_message" ? [] : [ev],
        firstEvent: ev,
      });
      continue;
    }
    if (ev.event_type === "user_message") {
      g.userMessage = ev.payload ?? null;
    } else {
      g.wireEvents.push(ev);
    }
    if (compareRunStart(ev, g.firstEvent) < 0) g.firstEvent = ev;
  }

  return [...groups.values()]
    .sort(
      (a, b) =>
        compareRunStart(a.firstEvent, b.firstEvent) ||
        a.runId.localeCompare(b.runId),
    )
    .map(({ runId, userMessage, wireEvents }) => ({
      runId,
      userMessage,
      wireEvents: wireEvents.sort(compareWithinRun),
    }));
}

/** One replayed turn of a session — selection, listing  or
 *  chat (general-agent-chat). The kind comes from the run's user_message
 *  payload discriminator (``kind: "listing_turn"`` / ``"chat_turn"``);
 *  legacy sessions without it replay as selection. */
export type SessionReplayEntry =
  | {
      kind: "selection";
      runId: string;
      query: string;
      userMessage: Record<string, unknown> | null;
      run: RunState;
    }
  | {
      kind: "listing";
      runId: string;
      query: string;
      userMessage: Record<string, unknown> | null;
      listingRun: ListingRunState;
    }
  | {
      kind: "chat";
      runId: string;
      query: string;
      userMessage: Record<string, unknown> | null;
      chatRun: ChatRunState;
    };

/** fold each run's wire events into its own fresh run state
 *  (pure function). Same events in → same array of entries out.
 *
 *  Pin test covers equivalence with a hand-rolled ``applyEvent`` loop so a
 *  future refactor that breaks parity fails SEAM.
 *
 *  ``includeFailed=false`` (default) skips error events so the UI never shows
 *  a stale error card from a half-completed earlier run; the run's other
 *  events are still applied so its nodes reflect where it stopped. Pass
 *  ``includeFailed=true`` for audit views.
 *
 *  Multi-run replay returns one entry per run, replacing the previous
 *  single-fold shape. Backfill callers (e.g. workbench openSession) read the
 *  *latest* selection entry's user_message for form fields.
 *
 *   a run whose user_message payload carries ``kind:
 *  "listing_turn"`` folds via applyListingEvent instead of applyEvent, so
 *  reopening a task shows its listing turns too. Legacy sessions have no
 *  listing events at all and are unaffected.
 */
export function replaySessionEvents(
  events: SessionEvent[],
  options: { includeFailed?: boolean } = {},
): SessionReplayEntry[] {
  const runs = splitSessionEvents(events);
  return runs.flatMap(({ runId, userMessage, wireEvents }): SessionReplayEntry[] => {
    const query =
      typeof userMessage?.["query"] === "string"
        ? (userMessage["query"] as string)
        : "(空)";

    if (userMessage?.["kind"] === "chat_turn") {
      // general-agent-chat — fold the chat turn (text + tool trace + cards).
      // includeFailed applies the same way as selection: error events are
      // skipped by default so a stale error never blocks the composer.
      const chatEvents = wireEvents.filter(
        (ev) => options.includeFailed || !ev.answer_failed,
      );
      let chatRun = chatRunFromReplay(
        query,
        chatEvents.map((ev) => ({
          createdAt: ev.created_at,
          payload: ev.payload as ChatWireEvent,
        })),
      );
      // chatRunFromReplay already demotes running→cancelled; keep the type
      // narrow here so callers never see a live replay run.
      if (chatRun.phase === "running") {
        chatRun = { ...chatRun, phase: "cancelled" };
      }
      return [{ kind: "chat", runId, query, userMessage, chatRun }];
    }

    if (userMessage?.["kind"] === "listing_turn") {
      const candidate = userMessage["candidate"] as ProductCandidate | undefined;
      if (!candidate || typeof candidate !== "object") {
        // Defensive: the server contract always writes the candidate; a
        // malformed row can't rebuild ListingRunState, so skip the turn.
        return [];
      }
      const brand =
        typeof userMessage["brand"] === "string"
          ? (userMessage["brand"] as string)
          : "";
      const startedAt =
        wireEvents.length > 0
          ? Date.parse(wireEvents[0].created_at) || Date.now()
          : Date.now();
      let listingRun: ListingRunState = {
        ...newListingRun(candidate, brand, []),
        startedAt,
      };
      for (const ev of wireEvents) {
        if (!options.includeFailed && ev.answer_failed) continue;
        if (ev.payload) {
          listingRun = applyListingEvent(listingRun, ev.payload as ListingWireEvent);
        }
      }
      // A replayed run is never live. With error events filtered by default
      // (include_failed=false at the API), a failed run would otherwise fold
      // to a ghost "running" phase and lock the composer (busy) forever.
      if (listingRun.phase === "running") {
        listingRun = { ...listingRun, phase: "cancelled" };
      }
      return [
        { kind: "listing", runId, query, userMessage, listingRun },
      ];
    }

    let run = newRun(query);
    for (const ev of wireEvents) {
      if (!options.includeFailed && ev.answer_failed) continue;
      if (ev.payload) {
        // payload is the full wire event (already includes `event` field); applyEvent
        // switches on that key, so we pass it through verbatim.
        run = applyEvent(run, ev.payload as WireEvent);
      }
    }
    // A replayed run is never live. With error events filtered by default
    // (include_failed=false at the API), a failed/aborted run would otherwise
    // fold to a ghost "running" phase and lock the composer (busy) forever.
    // Mirror the listing-side demotion (see above) so opening any session
    // never re-runs it visually.
    if (run.phase === "running") {
      run = { ...run, phase: "cancelled" };
    }
    return [{ kind: "selection", runId, query, userMessage, run }];
  });
}

function formatNodeDetail(summary: string, durationMs: number): string {
  const base = summary || "完成";
  return durationMs > 0 ? `${base} · ${(durationMs / 1000).toFixed(1)}s` : base;
}

/** Fold one NDJSON wire event into the run state (pure). */
export function applyEvent(run: RunState, event: WireEvent): RunState {
  switch (event.event) {
    case "node_start":
      return {
        ...run,
        nodes: run.nodes.map((n) =>
          n.id === event.node
            ? { ...n, status: "running", detail: "执行中…" }
            : n,
        ),
      };

    case "tool_call": {
      // loop runs carry a real tool trajectory per turn; legacy graph
      // runs keep the plain flat list. Discriminator: agentSkill was set by
      // the first turn_start of the loop run.
      if (run.agentSkill) {
        const turns = [...run.turns];
        if (turns.length === 0) return run; // tool before any turn — ignore
        const last = turns[turns.length - 1];
        turns[turns.length - 1] = {
          ...last,
          toolCalls: [
            ...last.toolCalls,
            { tool: event.tool, args: event.args, at: Date.now() },
          ],
        };
        return { ...run, turns };
      }
      return {
        ...run,
        toolCalls: [
          ...run.toolCalls,
          { tool: event.tool, args: event.args, at: Date.now() },
        ],
      };
    }

    case "tool_result": {
      if (!run.agentSkill) return run; // legacy runs have no tool_result wire
      const turns = [...run.turns];
      if (turns.length === 0) return run;
      const last = turns[turns.length - 1];
      const calls = [...last.toolCalls];
      if (calls.length === 0) return run;
      const head = calls[calls.length - 1];
      calls[calls.length - 1] = {
        ...head,
        ok: event.ok,
        duration_ms: event.duration_ms,
        summary: event.summary,
      };
      turns[turns.length - 1] = { ...last, toolCalls: calls };
      return { ...run, turns };
    }

    // loop-run macro events. First turn_start switches the macro card
    // from the five-node DAG to the single "选品 Agent" skill.
    case "turn_start":
      return {
        ...run,
        agentSkill: {
          status: "running",
          detail: run.turns.length === 0 ? "开始执行" : `第 ${run.turns.length + 1} 轮`,
        },
        turns: [
          ...run.turns,
          { toolCalls: [], status: "running" },
        ],
      };

    case "agent_delta":
      if (run.agentSkill) {
        return { ...run, agentAnswer: run.agentAnswer + event.delta };
      }
      return run;

    case "node_end":
      return {
        ...run,
        nodes: run.nodes.map((n) =>
          n.id === event.node
            ? {
                ...n,
                status: "done",
                detail: formatNodeDetail(event.summary, event.duration_ms),
              }
            : n,
        ),
        intent:
          event.intent !== undefined ? (event.intent ?? null) : run.intent,
      };

    case "report_chunk":
      return { ...run, streamedReport: run.streamedReport + event.delta };

    case "final": {
      const loop = run.agentSkill !== null;
      return {
        ...run,
        phase: "final",
        report: event.report,
        candidates: event.candidates,
        decision: event.decision,
        // loop run completes: skill card done; turns all done.
        agentSkill: loop
          ? { ...run.agentSkill!, status: "done", detail: "完成" }
          : run.agentSkill,
        turns: loop
          ? run.turns.map((t) =>
              t.status === "running" ? { ...t, status: "done" } : t,
            )
          : run.turns,
      };
    }

    case "empty":
      return {
        ...run,
        phase: "empty",
        emptyKeyword: event.keyword,
        emptyMessage: event.message,
      };

    case "error":
      return {
        ...run,
        phase: "error",
        errorInfo: {
          code: event.code,
          message: event.message,
          status: event.status,
          missing: event.missing,
        },
      };

    // compaction is session-level metadata, not per-run. Apply is
    // a no-op for the RunState (the summary lives on the session timeline;
    // the agent run whose events were summarized keeps its phase/decision).
    case "compaction":
    default:
      return run;
  }
}
