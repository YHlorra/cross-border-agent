"use client";

import type { PipelineNode, ToolTrace } from "./run-state";
import { IconCheck } from "@/components/icons";
import { RunTimeline } from "./run-timeline";

interface RunProgressProps {
  nodes: PipelineNode[];
  toolCalls: ToolTrace[];
  /** Whether the parent run is live — drives running-row timers. */
  running?: boolean;
}

/**
 * Think + tool detail — the agent's internal process. Always rendered
 * inside a collapsed <details>; the live surface shows only the thinking
 * ring + current step (see AgentRunMessage).
 *
 * ui-refresh the flat tool list became the grouped, expandable
 * RunTimeline (exec_cell grouping; interrupted synthesis lives in
 * toolTrace).
 */
export function RunProgress({ nodes, toolCalls, running = false }: RunProgressProps) {
  return (
    <div className="space-y-3">
      <ol className="overflow-hidden rounded-xl border border-border-muted bg-card">
        {nodes.map((step, i) => (
          <li
            key={step.id}
            className={`flex items-center gap-3 px-4 py-2.5 ${
              i > 0 ? "border-t border-border-muted" : ""
            } ${
              step.status === "running"
                ? "bg-primary-subtle"
                : step.status === "pending"
                  ? "opacity-45"
                  : ""
            }`}
          >
            <span className="flex h-5 w-5 shrink-0 items-center justify-center">
              {step.status === "done" && (
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary-soft text-[10px] text-accent">
                  <IconCheck width={12} height={12} />
                </span>
              )}
              {step.status === "running" && (
                <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
              )}
              {step.status === "pending" && (
                <span className="text-[10px] font-medium tabular-nums text-foreground-subtle">
                  {i + 1}
                </span>
              )}
            </span>
            <span className="text-[13px] font-medium text-foreground">
              {step.label}
            </span>
            <span className="ml-auto truncate text-xs text-foreground-subtle">
              {step.detail}
            </span>
          </li>
        ))}
      </ol>

      {toolCalls.length > 0 && (
        <div className="rounded-xl border border-border-muted bg-card px-4 py-3">
          <p className="mb-2 text-xs font-medium text-foreground-subtle">
            工具调用 · {toolCalls.length} 次
          </p>
          <RunTimeline calls={toolCalls} running={running} />
        </div>
      )}
    </div>
  );
}
