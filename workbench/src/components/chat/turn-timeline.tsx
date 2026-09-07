"use client";

import { useEffect, useState } from "react";

import { Markdown } from "./markdown";
import type { AgentTurn, TurnToolCall } from "./run-state";

interface TurnTimelineProps {
  turns: AgentTurn[];
  /** The accumulated model answer for the still-running turn. Empty when
   *  the run is finalised. */
  streamingAnswer: string;
  /** True while the agent is still emitting — drives the pulsing caret. */
  running: boolean;
}

/**
 * Per-turn ReAct narrative.
 *
 * Renders the model ↔ tool alternation as a vertical timeline:
 *   第 1 轮 · 模型输出  (cumulative delta text)
 *       ↳ 工具 hot_filter ✓ 1.4s · 11 通过
 *   第 2 轮 · 模型输出  (cumulative delta text)
 *       ↳ 工具 quality_score ✓ 1.7s · 11 评分完成
 *
 * Always rendered when there is at least one turn (so the process sense
 * is visible without expanding a details), but the streamed answer block
 * stays inside the main "思考中" surface above to avoid duplicating text.
 * 折叠默认 → 保持原有"细节可省"节奏.
 */
export function TurnTimeline({ turns, streamingAnswer, running }: TurnTimelineProps) {
  if (turns.length === 0) return null;
  return (
    <ol
      className="overflow-hidden rounded-xl border border-border-muted bg-card"
      data-turn-timeline
    >
      {turns.map((turn, i) => {
        const isLast = i === turns.length - 1;
        const liveDelta = isLast && running ? streamingAnswer : "";
        return (
          <li
            key={i}
            className={`px-4 py-3 ${i > 0 ? "border-t border-border-muted" : ""}`}
          >
            <div className="flex items-center gap-2 text-xs">
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary-soft font-mono text-[10px] text-accent">
                {i + 1}
              </span>
              <span className="font-medium text-foreground">模型输出</span>
              {isLast && running && (
                <span className="ml-1 text-foreground-subtle">
                  <span className="think-ring inline-block align-middle" /> 正在输出
                </span>
              )}
            </div>
            {liveDelta.length > 0 ? (
              <div className="mt-1.5 text-[13px] leading-relaxed text-foreground-muted">
                <Markdown text={liveDelta} />
                <span className="ml-0.5 inline-block h-3.5 w-[2px] animate-pulse bg-accent align-middle" />
              </div>
            ) : null}
            {turn.toolCalls.length === 0 ? (
              <p className="mt-1.5 text-[11px] text-foreground-subtle">
                无工具调用（终轮）
              </p>
            ) : (
              <ul className="mt-1.5 space-y-1">
                {turn.toolCalls.map((t, j) => (
                  <li
                    key={`${t.tool}-${j}`}
                    className="flex items-center gap-2 text-[12px] leading-relaxed"
                  >
                    <ToolGlyph call={t} />
                    <span className="font-mono text-foreground">{t.tool}</span>
                    {t.summary && (
                      <span className="truncate text-foreground-subtle">
                        {truncate(t.summary, 60)}
                      </span>
                    )}
                    {typeof t.duration_ms === "number" && t.duration_ms >= 0 && (
                      <span className="ml-auto shrink-0 tabular-nums text-foreground-subtle">
                        {(t.duration_ms / 1000).toFixed(1)}s
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function ToolGlyph({ call }: { call: TurnToolCall }) {
  if (call.ok === false) {
    return (
      <span
        className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-error/15 text-[10px] text-error"
        aria-label="工具失败"
      >
        ✗
      </span>
    );
  }
  if (call.ok === true) {
    return (
      <span
        className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-accent/15 text-[10px] text-accent"
        aria-label="工具成功"
      >
        ✓
      </span>
    );
  }
  // still in flight (no result yet)
  return (
    <span
      className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary-soft"
      aria-label="工具运行中"
    >
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
    </span>
  );
}

function truncate(s: string, n: number): string {
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}
