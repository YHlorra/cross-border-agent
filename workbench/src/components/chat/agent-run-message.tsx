"use client";

import { useEffect, useState } from "react";

import { ErrorView } from "@/components/views/error-view";
import { ResultView } from "@/components/views/result-view";
import type { ScoredCandidate } from "@/lib/selection-types";
import { prefLabel } from "@/lib/pref-labels";
import { Markdown } from "./markdown";
import { IconChevronRight, IconCircleSlash, IconLogo } from "@/components/icons";
import { RunProgress } from "./run-progress";
import { TurnTimeline } from "./turn-timeline";
import { displayNodes, toolTrace, type RunState } from "./run-state";

/** User turn — iMessage-style accent bubble, right aligned. */
export function UserMessage({ text }: { text: string }) {
  return (
    <div className="flex justify-end stagger-in">
      <div className="max-w-[78%] rounded-lg bg-accent px-4 py-2.5 text-[15px] leading-relaxed text-primary-foreground">
        {text}
      </div>
    </div>
  );
}

/** Ticking elapsed seconds while the run is live. */
function useElapsed(startedAt: number, active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return Math.max(0, Math.round((now - startedAt) / 1000));
}

interface AgentRunMessageProps {
  run: RunState;
  onRetry: () => void;
  onGoConfig: () => void;
  onGoListing?: (candidate: ScoredCandidate) => void;
  /** ui-refresh fill the composer with a follow-up (nitin action-chips). */
  onAsk?: (text: string) => void;
}

/** Agent turn — wraps one run's full lifecycle as chat content. */
export function AgentRunMessage({
  run,
  onRetry,
  onGoConfig,
  onGoListing,
  onAsk,
}: AgentRunMessageProps) {
  const running = run.phase === "running";
  const elapsed = useElapsed(run.startedAt, running);

  // macro/micro split: loop runs render the single agent-skill card +
  // the flattened tool trajectory; graph runs keep the five-node DAG.
  const nodes = displayNodes(run);
  const activeNode = nodes.find((n) => n.status === "running");
  const trace = toolTrace(run);
  const doneCount = nodes.filter((n) => n.status === "done").length;
  const reportStarted =
    (run.nodes.some((n) => n.id === "report" && n.status !== "pending") &&
      run.streamedReport.length > 0) ||
    run.agentAnswer.length > 0;
  const streamed = run.agentSkill ? run.agentAnswer : run.streamedReport;

  return (
    <div className="flex gap-3 stagger-in">
      <div
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-sm ${
          running ? "" : "bg-primary-soft text-accent"
        }`}
        aria-hidden
      >
        {running ? <span className="think-ring" /> : <IconLogo width={18} height={18} />}
      </div>

      <div className="min-w-0 flex-1 space-y-3">
        {/* Live surface while working: thinking ring + current step. */}
        {running && (
          <p className="text-sm text-foreground-muted">
            {reportStarted ? "正在生成答案" : "思考中"}
            {activeNode ? ` · ${activeNode.label}` : ""}…
            <span className="ml-2 tabular-nums text-foreground-subtle">
              {elapsed}s
            </span>
          </p>
        )}

        {/* Intent card — what the agent understood */}
        {run.intent && (
          <div className="inline-flex max-w-full flex-wrap items-center gap-2 rounded-xl border border-border-muted bg-card px-3.5 py-2 text-xs text-foreground-muted">
            <span className="font-medium text-foreground">
              「{run.intent.seed_keyword}」
            </span>
            {run.intent.budget_cny != null && (
              <span className="rounded-md bg-background-muted px-2 py-0.5">
                预算 ¥{run.intent.budget_cny}
              </span>
            )}
            {(run.intent.preferences ?? []).map((p) => (
              <span key={p} className="rounded-md bg-background-muted px-2 py-0.5">
                {/* show the human label instead of the wire key
                    ("light" → "轻小件"). Transmission literal stays canonical
                    — see prefMapOf("价格<50元" no-space variant) which the
                    intent alias table matches against. */}
                {prefLabel(p)}
              </span>
            ))}
            {run.intent.confidence && (
              <span className="text-foreground-subtle">
                · 置信 {run.intent.confidence}
              </span>
            )}
          </div>
        )}

        {/* Per-turn ReAct narrative (model ↔ tool alternation). Inline
            for loop runs so the process sense is visible without expanding a
            details. Falls back to nothing for legacy graph runs (turns=[]). */}
        {run.agentSkill && run.turns.length > 0 && (
          <TurnTimeline
            turns={run.turns}
            streamingAnswer={run.agentAnswer}
            running={running}
          />
        )}

        {/* Think + tool process — always collapsed by default */}
        <details className="group">
          <summary className="cursor-pointer select-none list-none text-xs font-medium text-foreground-subtle transition-colors hover:text-foreground">
            <span className="mr-1.5 inline-block transition-transform group-open:rotate-90">
              <IconChevronRight width={12} height={12} />
            </span>
            {running
              ? `思考过程 · ${doneCount}/${nodes.length} 步`
              : `执行过程 · ${doneCount}/${nodes.length} 步 · 工具 ${trace.length} 次`}
          </summary>
          <div className="mt-3">
            <RunProgress nodes={nodes} toolCalls={trace} running={running} />
          </div>
        </details>

        {/* Streaming output — markdown rendered as it arrives */}
        {running && reportStarted && (
          <div className="rounded-xl border border-border-muted bg-card px-4 py-3">
            <Markdown text={streamed} />
            <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-accent align-middle" />
          </div>
        )}

        {/* completed loop runs show the agent's full markdown answer
            (the structured result card below is fed from the final envelope). */}
        {run.phase === "final" && run.agentSkill && streamed.length > 0 && (
          <div className="rounded-xl border border-border-muted bg-card px-4 py-3">
            <Markdown text={streamed} />
          </div>
        )}

        {/* ui-refresh follow-up chips fill (never auto-send) the composer. */}
        {run.phase === "final" && run.agentSkill && onAsk && (
          <div className="flex flex-wrap gap-1.5">
            <Chip label="换个品类再选" onClick={() => onAsk("/选品 ")} />
            {run.candidates.length > 0 && (
              <Chip
                label="分析 Top 1 风险"
                onClick={() =>
                  onAsk(
                    `深入分析「${run.candidates[0].name_cn}」的主要风险与切入点`,
                  )
                }
              />
            )}
            {run.candidates.length > 0 && (
              <Chip
                label="转 Listing 生成"
                onClick={() =>
                  onAsk(
                    `为「${run.candidates[0].name_en || run.candidates[0].name_cn}」生成 Amazon Listing 草稿`,
                  )
                }
              />
            )}
          </div>
        )}

        {/* Terminal states */}
        {run.phase === "final" && (
          <ResultView
            query={run.query}
            report={run.report}
            candidates={run.candidates}
            decision={run.decision}
            onGoListing={onGoListing}
          />
        )}

        {run.phase === "empty" && (
          <div className="rounded-xl border border-border-muted bg-card px-5 py-6 text-center">
            <IconCircleSlash width={26} height={26} className="mx-auto mb-2 text-foreground-subtle" />
            <p className="text-sm font-medium text-foreground">
              没有找到匹配的商品
            </p>
            <p className="mx-auto mt-1 max-w-md text-[13px] leading-relaxed text-foreground-muted">
              {run.emptyMessage || "该品类当前不在数据集覆盖范围内。"}
              {run.emptyKeyword && (
                <>
                  {" "}
                  检索词：
                  <span className="rounded bg-background-muted px-1.5 py-0.5 font-mono text-xs">
                    {run.emptyKeyword}
                  </span>
                </>
              )}
            </p>
          </div>
        )}

        {run.phase === "error" && run.errorInfo && (
          <ErrorView
            query={run.query}
            code={run.errorInfo.code}
            message={run.errorInfo.message}
            status={run.errorInfo.status}
            missing={run.errorInfo.missing}
            onRetry={onRetry}
            onBack={() => {}}
            onGoConfig={onGoConfig}
          />
        )}

        {run.phase === "cancelled" && (
          <p className="text-xs text-foreground-subtle">
            已停止本次运行（{elapsed}s）
          </p>
        )}
      </div>
    </div>
  );
}

function Chip({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-md border border-border-muted bg-card px-3 py-1 text-xs text-foreground-muted shadow-card transition-colors hover:border-accent/40 hover:text-foreground"
    >
      {label}
    </button>
  );
}
