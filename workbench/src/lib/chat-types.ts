/**
 * Wire types for the general-agent chat channel (general-agent-chat D4).
 * Shares the NDJSON vocabulary with selection runs (turn_start / agent_delta /
 * tool_call / tool_result / error) and adds exactly one event: `card`. The
 * chat `final` carries `kind:"chat"` plus the assistant text.
 */

/** Reuse the shared tool/turn event shapes (structurally identical fields). */
import type { TurnToolCall } from "@/components/chat/turn-types";

export interface SelectionCard {
  type: "selection";
  run_id: string;
  decision: string;
  report: import("./selection-types").SelectionReport | null;
  candidates: import("./selection-types").ScoredCandidate[];
  market_summary: string;
}

export interface ListingCard {
  type: "listing";
  run_id: string;
  product_id: string;
  candidate: import("./selection-types").ProductCandidate;
  listing: Record<string, unknown>;
  issues: unknown[];
  hard_failed: boolean;
}

export type ChatCard = SelectionCard | ListingCard;

export type ChatWireEvent =
  | { event: "turn_start"; turn: number; tool_calls_used: number }
  | { event: "agent_delta"; delta: string; turn: number }
  | { event: "tool_call"; tool: string; args: Record<string, unknown>; turn: number }
  | {
      event: "tool_result";
      tool: string;
      ok: boolean;
      duration_ms: number;
      summary?: string;
      error?: string | null;
    }
  | { event: "card"; card: ChatCard }
  | {
      event: "final";
      kind: "chat";
      text: string;
      session_id: string;
      run_id: string;
    }
  | {
      event: "error";
      code: string;
      message: string;
      status?: number;
    };

/** Type guard so generic NDJSON clients can route chat frames. */
export function isChatFinal(ev: { event: string }): ev is Extract<
  ChatWireEvent,
  { event: "final" }
> {
  return ev.event === "final" && (ev as { kind?: string }).kind === "chat";
}

/** Unused-import guard: TurnToolCall is re-exported for chat-run-state. */
export type { TurnToolCall };
