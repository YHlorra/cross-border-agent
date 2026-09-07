// Shared agent-loop turn/tool types (general-agent-chat 2.4) — extracted
// from run-state.ts so chat-run-state can reuse them without a circular
// import (run-state ↔ chat-run-state). run-state re-exports these names, so
// existing imports keep working.

/** a loop-run tool trace entry: dynamic, resolves via tool_result. */
export interface TurnToolCall {
  tool: string;
  args: Record<string, unknown>;
  /** Client arrival time — anchors the running-row timer. */
  at?: number;
  ok?: boolean;
  duration_ms?: number;
  summary?: string;
}

/** one model turn of a create_agent run (macro card + trajectories). */
export interface AgentTurn {
  toolCalls: TurnToolCall[];
  status: "running" | "done";
}

/** a resolved tool trace line for the UI (graph runs + loop runs). */
export interface ToolTrace {
  tool: string;
  args: Record<string, unknown>;
  /** ui-refresh client arrival time of the tool_call event. */
  at?: number;
  ok?: boolean;
  duration_ms?: number;
  summary?: string;
  /** ui-refresh run hit a terminal phase before tool_result landed
   *  (codex `mark_failed` rule): rendered as 已中断, never as running. */
  interrupted?: boolean;
}

/** Fold tool_call/tool_result frames into the last turn of `turns` —
 *  shared fold rules for selection and chat runs. Pure. */
export function applyToolEventToTurns(
  turns: AgentTurn[],
  event:
    | { event: "tool_call"; tool: string; args: Record<string, unknown> }
    | {
        event: "tool_result";
        ok: boolean;
        duration_ms: number;
        summary?: string;
      },
): AgentTurn[] {
  if (event.event === "tool_call") {
    if (turns.length === 0) return turns; // tool before any turn — ignore
    const next = [...turns];
    const last = next[next.length - 1];
    next[next.length - 1] = {
      ...last,
      toolCalls: [
        ...last.toolCalls,
        { tool: event.tool, args: event.args, at: Date.now() },
      ],
    };
    return next;
  }
  if (turns.length === 0) return turns;
  const next = [...turns];
  const last = next[next.length - 1];
  const calls = [...last.toolCalls];
  if (calls.length === 0) return next;
  const head = calls[calls.length - 1];
  calls[calls.length - 1] = {
    ...head,
    ok: event.ok,
    duration_ms: event.duration_ms,
    summary: event.summary,
  };
  next[next.length - 1] = { ...last, toolCalls: calls };
  return next;
}

/** Flatten turns into a UI trace list (toolTrace's turns branch). */
export function flatTurnTrace(turns: AgentTurn[]): ToolTrace[] {
  return turns.flatMap((t) => t.toolCalls);
}
