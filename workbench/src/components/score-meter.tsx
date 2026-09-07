"use client";

import type { DimensionScore as DimensionScoreType } from "@/lib/selection-types";
import { dimensions } from "@/lib/mock-data";

interface ScoreMeterProps {
  scores: DimensionScoreType[];
  compact?: boolean;
}

export function ScoreMeter({ scores, compact }: ScoreMeterProps) {
  return (
    <div className="space-y-2">
      {scores.map((s) => {
        const dim = dimensions.find((d) => d.key === s.dimension);
        const pct = (s.score / 10) * 100;
        const color =
          s.score >= 7.5
            ? "bg-accent"
            : s.score >= 6
              ? "bg-warning"
              : "bg-error";
        return (
          <div key={s.dimension} className="group">
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="text-foreground-muted">
                {dim?.label ?? s.dimension}
              </span>
              <span className="tabular-nums font-medium text-foreground">
                {s.score.toFixed(1)}
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-background-muted">
              <div
                className={`score-fill h-full rounded-full ${color}`}
                style={{ width: `${pct}%`, animationDelay: "0.1s" }}
              />
            </div>
            {!compact && (
              <p className="mt-1 text-[11px] leading-relaxed text-foreground-subtle opacity-0 transition-opacity group-hover:opacity-100">
                {s.reason}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}