"use client";

import { Button } from "@appica/ui-react/button";
import { Card } from "@appica/ui-react/card";
import { BackgroundPattern } from "@appica/ui-react/background-pattern";

import { IconCheck, IconChevronRight } from "@/components/icons";

export interface PipelineNode {
  id: string;
  label: string;
  detail: string;
  status: "pending" | "running" | "done";
}

export interface ToolCallEvent {
  tool: string;
  args: Record<string, unknown>;
  at: number;
}

interface RunningViewProps {
  query: string;
  nodes: PipelineNode[];
  toolCalls: ToolCallEvent[];
  streamedReport: string;
  onCancel: () => void;
}

export function RunningView({
  query,
  nodes,
  toolCalls,
  streamedReport,
  onCancel,
}: RunningViewProps) {
  const activeNode = nodes.find((n) => n.status === "running");
  const reportNode = nodes.find((n) => n.id === "report");

  return (
    <div className="mx-auto max-w-2xl px-6 py-8">
      <div className="mb-6 flex items-center gap-3 stagger-in">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary-soft">
          <span className="eq-bar h-3" />
          <span className="eq-bar h-3" />
          <span className="eq-bar h-3" />
          <span className="eq-bar h-3" />
        </div>
        <div className="flex-1">
          <p className="text-sm font-medium text-foreground">
            正在分析「{query}」
          </p>
          <p className="text-xs text-foreground-subtle">
            {activeNode
              ? `当前步骤：${activeNode.label}`
              : "Agent 正在执行选品流程"}
          </p>
        </div>
        <Button
          variant="secondary"
          className="rounded-md bg-background-muted px-4 text-xs hover:bg-background-strong"
          onClick={onCancel}
        >
          取消
        </Button>
      </div>

      <div className="mb-6 space-y-2">
        {nodes.map((step, i) => (
          <Card
            key={step.id}
            frame={false}
            className={`stagger-in flex items-start gap-4 p-4 [--card-radius:var(--radius-md)] ${
              step.status === "done"
                ? "bg-card"
                : step.status === "running"
                  ? "bg-card ring-1 ring-accent/30"
                  : "bg-card/50 opacity-50"
            }`}
            style={{ animationDelay: `${i * 80}ms` }}
          >
            <div className="mt-0.5 shrink-0">
              {step.status === "done" && (
                <div className="flex h-6 w-6 items-center justify-center rounded-full bg-primary-soft text-accent">
                  <IconCheck width={12} height={12} />
                </div>
              )}
              {step.status === "running" && (
                <div className="flex h-6 w-6 items-end justify-center gap-0.5 rounded-full bg-primary-soft py-1.5">
                  <span className="eq-bar" />
                  <span className="eq-bar" />
                  <span className="eq-bar" />
                  <span className="eq-bar" />
                </div>
              )}
              {step.status === "pending" && (
                <div className="flex h-6 w-6 items-center justify-center rounded-full border border-border text-xs text-foreground-subtle">
                  {i + 1}
                </div>
              )}
            </div>

            <div className="flex-1">
              <p className="text-sm font-medium text-foreground">{step.label}</p>
              <p className="mt-0.5 text-xs text-foreground-muted">
                {step.detail || (step.status === "running" ? "执行中…" : "等待中")}
              </p>
            </div>
          </Card>
        ))}
      </div>

      {toolCalls.length > 0 && (
        <Card
          frame={false}
          className="stagger-in mb-6 overflow-hidden [--card-radius:var(--radius-md)]"
          style={{ animationDelay: "300ms" }}
        >
          <BackgroundPattern
            variant="dots"
            spotlight
            className="bg-card p-4"
          >
            <p className="mb-3 text-xs font-semibold text-foreground-subtle">
              调用轨迹
            </p>
            <ul className="space-y-1.5">
              {toolCalls.slice(-12).map((t, i) => (
                <li
                  key={`${t.tool}-${t.at}-${i}`}
                  className="flex gap-2 font-mono text-[11px] leading-relaxed text-foreground-muted"
                >
                  <span className="text-accent"><IconChevronRight width={12} height={12} className="inline align-[-2px]" /></span>
                  <span className="text-foreground">{t.tool}</span>
                  <span className="truncate text-foreground-subtle">
                    {summarizeArgs(t.args)}
                  </span>
                </li>
              ))}
            </ul>
          </BackgroundPattern>
        </Card>
      )}

      {reportNode && streamedReport.length > 0 && (
        <Card
          frame={false}
          className="stagger-in bg-card p-5 [--card-radius:var(--radius-md)]"
          style={{ animationDelay: "350ms" }}
        >
          <p className="mb-2 text-xs font-semibold text-foreground-subtle">
            报告生成中…
          </p>
          <div className="max-h-64 overflow-y-auto whitespace-pre-wrap text-sm leading-relaxed text-foreground">
            {streamedReport}
            <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-accent align-middle" />
          </div>
        </Card>
      )}
    </div>
  );
}

function summarizeArgs(args: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(args)) {
    if (v == null || v === "") continue;
    if (typeof v === "object") {
      parts.push(`${k}={…}`);
    } else {
      parts.push(`${k}=${String(v)}`);
    }
  }
  return parts.length ? parts.join(", ") : "";
}