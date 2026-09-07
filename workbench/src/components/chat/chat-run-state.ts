/**
 * Chat run state model (general-agent-chat D6) — one entry wraps the full
 * lifecycle of a general-agent conversation turn: streamed assistant text,
 * the tool trajectory, and any selection/listing cards emitted by domain
 * tools. Pure logic, no JSX. Fold rules mirror run-state.ts's loop-run path.
 */

import type { ChatCard, ChatWireEvent } from "@/lib/chat-types";
import type { ErrorInfo, RunPhase } from "./run-state";
import {
  applyToolEventToTurns,
  flatTurnTrace,
  type AgentTurn,
  type ToolTrace,
} from "./turn-types";

export type { AgentTurn, ToolTrace };

export interface ChatRunState {
  query: string;
  phase: RunPhase;
  startedAt: number;
  turns: AgentTurn[];
  /** Streamed assistant text (agent_delta accumulation, finalized by final). */
  text: string;
  /** Domain-tool cards in arrival order (selection report / listing draft). */
  cards: ChatCard[];
  errorInfo: ErrorInfo | null;
}

export function newChatRun(query: string): ChatRunState {
  return {
    query,
    phase: "running",
    startedAt: Date.now(),
    turns: [],
    text: "",
    cards: [],
    errorInfo: null,
  };
}

/** Flat tool trace for RunTimeline (same shape the selection run uses). */
export function chatToolTrace(run: ChatRunState): ToolTrace[] {
  const terminal = run.phase === "cancelled" || run.phase === "error";
  const synth = (t: ToolTrace): ToolTrace =>
    terminal && t.ok === undefined ? { ...t, ok: false, interrupted: true } : t;
  return flatTurnTrace(run.turns).map(synth);
}

/** Fold one NDJSON chat frame into the run state (pure). */
export function applyChatEvent(
  run: ChatRunState,
  event: ChatWireEvent,
): ChatRunState {
  switch (event.event) {
    case "turn_start":
      return {
        ...run,
        turns: [...run.turns, { toolCalls: [], status: "running" }],
      };

    case "tool_call":
    case "tool_result":
      return { ...run, turns: applyToolEventToTurns(run.turns, event) };

    case "agent_delta":
      return { ...run, text: run.text + event.delta };

    case "card":
      return { ...run, cards: [...run.cards, event.card] };

    case "final":
      return {
        ...run,
        phase: "final",
        // The final text is the loop's own last-message reconstruction —
        // authoritative over the streamed deltas (they may have been lossy).
        text: event.text || run.text,
        turns: run.turns.map((t) =>
          t.status === "running" ? { ...t, status: "done" } : t,
        ),
      };

    case "error":
      return {
        ...run,
        phase: "error",
        errorInfo: {
          code: event.code,
          message: event.message,
          status: event.status ?? null,
        },
      };

    default:
      return run;
  }
}

/** Rebuild a chat run from persisted session events (replay path). Live
 *  startedAt is replaced by the first event's created_at. */
export function chatRunFromReplay(
  query: string,
  events: { createdAt: string; payload: ChatWireEvent }[],
): ChatRunState {
  const startedAt =
    events.length > 0
      ? Date.parse(events[0].createdAt) || Date.now()
      : Date.now();
  let run: ChatRunState = { ...newChatRun(query), startedAt };
  for (const ev of events) {
    run = applyChatEvent(run, ev.payload);
  }
  // A replayed run is never live — a ghost "running" phase would lock the
  // composer forever (same demotion contract as selection/listing replay).
  if (run.phase === "running") {
    run = { ...run, phase: "cancelled" };
  }
  return run;
}
