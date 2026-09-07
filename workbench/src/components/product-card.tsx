"use client";

import { Card } from "@appica/ui-react/card";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";

import type { ScoredCandidate } from "@/lib/selection-types";
import { extractRiskTags, RiskTags } from "@/components/risk-tags";
import Sparkline from "@/components/charts/sparkline";

interface ProductCardProps {
  product: ScoredCandidate;
  index: number;
  onClick: () => void;
}

const recommendationConfig: Record<
  ScoredCandidate["recommendation"],
  { label: string; variant: "success" | "warning" | "error" }
> = {
  go: { label: "推荐", variant: "success" },
  caution: { label: "可选", variant: "warning" },
  "no-go": { label: "不推荐", variant: "error" },
};

export function ProductCard({ product, index, onClick }: ProductCardProps) {
  const rec = recommendationConfig[product.recommendation];

  return (
    <Card
      frame={false}
      className="card-hover group relative cursor-pointer overflow-hidden bg-card p-4 [--card-radius:var(--radius-md)]"
      style={{ animationDelay: `${index * 80}ms` }}
      onClick={onClick}
    >
      <div className="relative mb-4 aspect-square w-full overflow-hidden rounded-md bg-background-muted">
        {product.image_url ? (
          <img
            src={product.image_url}
            alt={product.name_en || product.name_cn}
            loading="lazy"
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-background-muted to-background-strong">
            <span className="text-4xl opacity-30">📦</span>
          </div>
        )}

        <Button
          size="icon-md"
          className="card-play-btn absolute bottom-2 right-2 h-12 w-12 rounded-full bg-accent text-primary-foreground shadow-lg hover:scale-105 hover:bg-accent-hover"
          onClick={(e) => {
            e.stopPropagation();
            onClick();
          }}
        >
          <span className="ml-0.5 text-lg">→</span>
        </Button>
      </div>

      <div className="space-y-1">
        <div className="flex items-start justify-between gap-2">
          <h3 className="line-clamp-2 text-sm font-semibold leading-snug text-foreground">
            {product.name_cn}
          </h3>
          <Badge variant={rec.variant} className="shrink-0 rounded-sm text-[10px]">
            {rec.label}
          </Badge>
        </div>
        <p className="truncate text-xs text-foreground-muted">
          {product.category}
        </p>
        <RiskTags tags={extractRiskTags({ risk_flags: product.risks })} />
        {product.trend_series && product.trend_series.length > 1 && (
          <div className="pt-1">
            <Sparkline series={product.trend_series} height={28} />
          </div>
        )}
        <div className="flex items-center gap-2 pt-1 text-xs text-foreground-subtle">
          <span className="tabular-nums">¥{product.source_price_cny}</span>
          <span>→</span>
          <span className="tabular-nums text-foreground-muted">
            ${product.target_price_usd}
          </span>
          <span className="ml-auto rounded bg-primary-soft px-1.5 py-0.5 text-[10px] font-medium text-accent">
            {product.price_gap_ratio.toFixed(1)}x
          </span>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-2 border-t border-border-muted pt-3">
        <span className="text-2xl font-bold tabular-nums text-foreground-intense">
          {product.total_score.toFixed(1)}
        </span>
        <div className="flex-1">
          <div className="h-1 overflow-hidden rounded-full bg-background-muted">
            <div
              className="h-full rounded-full bg-accent"
              style={{ width: `${product.total_score * 10}%` }}
            />
          </div>
          <p className="mt-1 text-[10px] text-foreground-subtle">综合评分</p>
        </div>
      </div>
    </Card>
  );
}