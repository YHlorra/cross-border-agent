"use client";

import { useEffect, useState } from "react";

import { IconCheck, IconChevronRight, IconX } from "@/components/icons";
import type { ToolTrace } from "./run-state";

/**
 * RunTimeline — the tool-call trajectory as an expandable
 * timeline: codex `exec_cell` grouping (consecutive successful reads merge
 * into one "资料检索" cell; submit_report never merges; any failure splits)
 * crossed with nitin `agent-timeline` row anatomy (status icon + mono tool
 * name + truncated summary + tabular duration, expandable Input/Output).
 *
 * Detail ceiling is honest: the wire carries args + a result summary, so
 * expansion shows exactly those — no invented data.
 */

/** Read-only lookup tools eligible for exec-cell grouping. */
const EXPLORE_TOOLS = new Set(["search_1688", "search_amazon", "category_trend"]);

/** codex MAX_GROUPED_COMMANDS. */
const MAX_GROUP = 32;

/** Detail truncation — nitin JsonBlock's 800-char cap. */
const DETAIL_CAP = 800;

type TimelineItem =
  | { kind: "group"; calls: ToolTrace[]; totalMs: number }
  | { kind: "single"; call: ToolTrace };

export function groupCalls(calls: ToolTrace[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  let buf: ToolTrace[] = [];
  const flush = () => {
    if (buf.length === 0) return;
    if (buf.length === 1) {
      items.push({ kind: "single", call: buf[0] });
    } else {
      items.push({
        kind: "group",
        calls: buf,
        totalMs: buf.reduce((s, c) => s + (c.duration_ms ?? 0), 0),
      });
    }
    buf = [];
  };
  for (const c of calls) {
    const groupable =
      EXPLORE_TOOLS.has(c.tool) && c.ok === true && !c.interrupted;
    if (!groupable) {
      flush();
      items.push({ kind: "single", call: c });
      continue;
    }
    buf.push(c);
    if (buf.length >= MAX_GROUP) flush();
  }
  flush();
  return items;
}

export function RunTimeline({
  calls,
  running,
}: {
  calls: ToolTrace[];
  running: boolean;
}) {
  const items = groupCalls(calls);
  if (items.length === 0) return null;
  return (
    <ol className="space-y-0.5">
      {items.map((item, i) =>
        item.kind === "group" ? (
          <GroupCell key={`g-${i}`} item={item} running={running} />
        ) : (
          <li key={`s-${i}`}>
            <CallRow call={item.call} running={running} />
          </li>
        ),
      )}
    </ol>
  );
}

/** One merged read-cell — expanded shows the per-call rows. */
function GroupCell({
  item,
  running,
}: {
  item: Extract<TimelineItem, { kind: "group" }>;
  running: boolean;
}) {
  return (
    <details className="group/gcell">
      <summary className="flex cursor-pointer select-none list-none items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-background-muted">
        <IconChevronRight
          width={12}
          height={12}
          className="shrink-0 text-foreground-subtle transition-transform group-open/gcell:rotate-90"
        />
        <span className="rounded bg-primary-soft px-1.5 py-0.5 text-[10px] font-medium text-accent">
          资料检索
        </span>
        <span className="text-[11px] text-foreground-muted">
          {item.calls.length} 次连续检索
        </span>
        <span className="ml-auto shrink-0 tabular-nums text-[11px] text-foreground-subtle">
          {(item.totalMs / 1000).toFixed(1)}s
        </span>
      </summary>
      <ol className="ml-4 border-l border-border-muted pl-1">
        {item.calls.map((c, i) => (
          <li key={i}>
            <CallRow call={c} running={running} nested />
          </li>
        ))}
      </ol>
    </details>
  );
}

function CallRow({
  call,
  running,
  nested = false,
}: {
  call: ToolTrace;
  running: boolean;
  nested?: boolean;
}) {
  const live = running && call.ok === undefined && !call.interrupted;
  const hasDetail = Object.keys(call.args ?? {}).length > 0 || !!call.summary;
  const failed = call.interrupted || call.ok === false;
  return (
    <details className="group/row" open={false}>
      <summary
        className={`flex cursor-pointer select-none list-none items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-background-muted ${
          nested ? "py-1" : ""
        }`}
      >
        {hasDetail && (
          <IconChevronRight
            width={12}
            height={12}
            className="shrink-0 text-foreground-subtle transition-transform group-open/row:rotate-90"
          />
        )}
        {!hasDetail && <span className="w-3 shrink-0" />}
        <StatusIcon call={call} live={live} />
        <span className="shrink-0 font-mono text-[11px] text-foreground">
          {call.tool}
        </span>
        <span className="min-w-0 flex-1 truncate text-[11px] text-foreground-subtle">
          {call.interrupted ? (
            <span className="text-error/90">已中断</span>
          ) : (
            (call.summary ?? summarizeArgs(call.args))
          )}
        </span>
        {live ? (
          <LiveElapsed since={call.at} />
        ) : typeof call.duration_ms === "number" && call.duration_ms > 0 ? (
          <span className="ml-auto shrink-0 tabular-nums text-[11px] text-foreground-subtle">
            {(call.duration_ms / 1000).toFixed(1)}s
          </span>
        ) : null}
      </summary>
      {hasDetail && (
        <div className="ml-5 mb-1 space-y-1.5 border-l border-border-muted pl-3 py-1">
          <DetailBlock label="Input" text={JSON.stringify(call.args, null, 2)} />
          {call.summary && (
            <DetailBlock label="Output" text={call.summary} />
          )}
        </div>
      )}
      {/* failed hint keeps failure legible even when the summary is empty */}
      {failed && !call.summary && (
        <p className="ml-5 pb-1 text-[11px] text-error/90">
          {call.interrupted ? "运行结束前未返回结果" : "执行失败"}
        </p>
      )}
    </details>
  );
}

function StatusIcon({ call, live }: { call: ToolTrace; live: boolean }) {
  if (live) {
    return <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-accent" />;
  }
  if (call.ok === false) {
    return <IconX width={12} height={12} className="shrink-0 text-error" />;
  }
  if (call.ok === true) {
    return <IconCheck width={12} height={12} className="shrink-0 text-accent" />;
  }
  // Legacy graph-run rows carry no ok — neutral marker (two-generation replay).
  return (
    <IconChevronRight width={12} height={12} className="shrink-0 text-accent" />
  );
}

/** Per-row ticking seconds while the run is live (anchored on `at`). */
function LiveElapsed({ since }: { since?: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  if (!since) return null;
  return (
    <span className="ml-auto shrink-0 tabular-nums text-[11px] text-foreground-subtle">
      {Math.max(0, Math.round((now - since) / 1000))}s
    </span>
  );
}

function DetailBlock({ label, text }: { label: string; text: string }) {
  const truncated = text.length > DETAIL_CAP;
  return (
    <div>
      <p className="mb-0.5 text-[10px] font-medium text-foreground-subtle">
        {label}
      </p>
      <pre className="max-h-40 overflow-auto rounded-md bg-background-subtle p-2 font-mono text-[10px] leading-relaxed text-foreground-muted">
        {truncated ? `${text.slice(0, DETAIL_CAP)}\n… +${text.length - DETAIL_CAP} 字符` : text}
      </pre>
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
