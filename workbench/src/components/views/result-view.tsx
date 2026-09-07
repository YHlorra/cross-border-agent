"use client";

import { useState } from "react";
import { Card } from "@appica/ui-react/card";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import {
  Drawer,
  DrawerContent,
  DrawerHeader,
  DrawerTitle,
  DrawerBody,
  DrawerFooter,
  DrawerClose,
} from "@appica/ui-react/drawer";
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@appica/ui-react/accordion";
import { Separator } from "@appica/ui-react/separator";

import type { ScoredCandidate, SelectionReport } from "@/lib/selection-types";
import { ScoreMeter } from "@/components/score-meter";
import { ProductCard } from "@/components/product-card";
import CompetitorCard from "@/components/competitor-card";
import { Markdown } from "@/components/chat/markdown";

interface ResultViewProps {
  query: string;
  report: SelectionReport | null;
  candidates: ScoredCandidate[];
  decision: string;
  /** 接通「转入 Listing 生成」— workbench 注入 submitListing。 */
  onGoListing?: (candidate: ScoredCandidate) => void;
}

const decisionBadge: Record<string, { label: string; variant: "success" | "warning" | "error" }> =
  {
    go: { label: "强烈推荐", variant: "success" },
    caution: { label: "可选", variant: "warning" },
    "no-go": { label: "不建议", variant: "error" },
  };

export function ResultView({ query, report, candidates, decision, onGoListing }: ResultViewProps) {
  const [selected, setSelected] = useState<ScoredCandidate | null>(null);

  const goCandidates = candidates.filter((c) => c.recommendation !== "no-go");
  const nogoCandidates = candidates.filter((c) => c.recommendation === "no-go");
  const badge = decisionBadge[decision] ?? decisionBadge["no-go"];
  // 底部主按钮的目标 = 推荐位第一个候选（无候选时按钮禁用）
  const topCandidate = goCandidates[0] ?? candidates[0] ?? null;

  return (
    <div className="py-1">
      <section className="mb-8 stagger-in">
        <div className="mb-2 flex items-center gap-3">
          <h2 className="text-xl font-bold tracking-tight">
            「{query}」选品报告
          </h2>
          {decision && (
            <Badge variant={badge.variant} size="md">
              {badge.label}
            </Badge>
          )}
        </div>
        {/* ui-refresh grounding: where the candidates come from. */}
        <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] text-foreground-subtle">
          <span className="rounded-md bg-background-muted px-2 py-0.5">
            1688 货源 × Amazon 竞品数据集
          </span>
          {candidates.length > 0 && (
            <span className="rounded-md bg-background-muted px-2 py-0.5 tabular-nums">
              {candidates.length} 候选 · {goCandidates.length} 推荐
            </span>
          )}
        </div>
        {report?.market_summary ? (
          <div className="max-w-3xl">
            <Markdown text={report.market_summary} />
          </div>
        ) : (
          <p className="max-w-3xl text-sm leading-relaxed text-foreground-muted">
            暂无市场概览。
          </p>
        )}
      </section>

      {candidates.length === 0 ? (
        <Card
          frame={false}
          className="bg-card p-8 text-center [--card-radius:var(--radius-md)]"
        >
          <p className="text-sm text-foreground-muted">
            本次查询没有返回候选商品。可能该品类当前不在数据集中。
          </p>
        </Card>
      ) : (
        <>
          {goCandidates.length > 0 && (
            <section
              className="mb-8 stagger-in"
              style={{ animationDelay: "100ms" }}
            >
              <div className="mb-4 flex items-center justify-between">
                <h3 className="text-base font-semibold">
                  推荐商品
                  <span className="ml-2 text-sm font-normal text-foreground-subtle">
                    {goCandidates.length} 个候选
                  </span>
                </h3>
              </div>
              <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
                {goCandidates.map((p, i) => (
                  <ProductCard
                    key={p.product_id}
                    product={p}
                    index={i}
                    onClick={() => setSelected(p)}
                  />
                ))}
              </div>
            </section>
          )}

          {nogoCandidates.length > 0 && (
            <section
              className="mb-8 stagger-in"
              style={{ animationDelay: "200ms" }}
            >
              <h3 className="mb-4 text-base font-semibold text-foreground-muted">
                不推荐
                <span className="ml-2 text-sm font-normal text-foreground-subtle">
                  {nogoCandidates.length} 个
                </span>
              </h3>
              <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
                {nogoCandidates.map((p, i) => (
                  <ProductCard
                    key={p.product_id}
                    product={p}
                    index={i + goCandidates.length}
                    onClick={() => setSelected(p)}
                  />
                ))}
              </div>
            </section>
          )}
        </>
      )}

      {candidates.length > 0 && (
        <section className="mb-8 stagger-in" style={{ animationDelay: "250ms" }}>
          <CompetitorCard
            title="候选竞品一览（Amazon 数据集）"
            candidates={candidates.map((c) => ({
              name: c.name_en || c.name_cn,
              price: c.target_price_usd,
              reviews: c.review_count,
            }))}
          />
        </section>
      )}

      <section className="mb-8 stagger-in" style={{ animationDelay: "300ms" }}>
        <Card frame={false} className="bg-card p-6 [--card-radius:var(--radius-md)]">
          <h3 className="mb-4 text-base font-semibold">行动建议</h3>
          <p className="text-sm text-foreground-muted">
            详见上方报告与每个候选商品详情中的机会点 / 风险点。建议在采购前实地验证供应商并小批量测试。
          </p>
          <Separator className="my-6 bg-border-muted" />
          <div className="flex gap-3">
            <Button
              className="rounded-md bg-accent px-6 text-accent-contrast hover:bg-accent-hover"
              onClick={() => exportReport(query, report, candidates, decision)}
            >
              导出报告
            </Button>
            <Button
              variant="secondary"
              className="rounded-md bg-background-muted px-6 hover:bg-background-strong"
              disabled={!onGoListing || !topCandidate}
              title={!topCandidate ? "没有可用的候选商品" : undefined}
              onClick={() => topCandidate && onGoListing?.(topCandidate)}
            >
              转入 Listing 生成 →
            </Button>
          </div>
        </Card>
      </section>

      <Drawer
        open={!!selected}
        onOpenChange={(open) => !open && setSelected(null)}
        side="bottom"
      >
        <DrawerContent className="h-[85vh] rounded-t-xl border-0 bg-card">
          {selected && (
            <>
              <DrawerHeader>
                <div className="flex items-start gap-4">
                  <div className="flex h-20 w-20 shrink-0 items-center justify-center rounded-lg bg-background-muted text-3xl">
                    📦
                  </div>
                  <div className="flex-1">
                    <Badge
                      variant={
                        selected.recommendation === "go"
                          ? "success"
                          : selected.recommendation === "caution"
                            ? "warning"
                            : "error"
                      }
                      className="mb-2 rounded-full"
                    >
                      {selected.recommendation === "go"
                        ? "推荐"
                        : selected.recommendation === "caution"
                          ? "可选"
                          : "不推荐"}
                    </Badge>
                    <DrawerTitle className="text-lg font-bold leading-snug text-foreground">
                      {selected.name_cn}
                    </DrawerTitle>
                    <p className="text-sm text-foreground-muted">
                      {selected.name_en}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-4 text-sm">
                      <span className="tabular-nums">
                        <span className="text-foreground-subtle">采购 </span>¥
                        {selected.source_price_cny}
                      </span>
                      <span className="tabular-nums">
                        <span className="text-foreground-subtle">售价 </span>$
                        {selected.target_price_usd}
                      </span>
                      <span className="rounded bg-primary-soft px-2 py-0.5 text-xs font-medium text-accent">
                        {selected.price_gap_ratio.toFixed(1)}x 价差
                      </span>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-4xl font-bold tabular-nums text-accent">
                      {selected.total_score.toFixed(1)}
                    </p>
                    <p className="text-xs text-foreground-subtle">综合评分</p>
                  </div>
                </div>
              </DrawerHeader>

              <DrawerBody>
                <div className="mb-6">
                  <h3 className="mb-3 text-sm font-semibold">五维评分</h3>
                  <ScoreMeter scores={selected.scores} />
                </div>

                <Accordion multiple className="mb-6">
                  <AccordionItem value="opps">
                    <AccordionTrigger className="text-sm font-semibold hover:no-underline">
                      <span className="text-accent">●</span> 机会点（
                      {selected.opportunities.length}）
                    </AccordionTrigger>
                    <AccordionContent>
                      <ul className="space-y-2 pb-3 pl-4">
                        {selected.opportunities.map((o, i) => (
                          <li
                            key={i}
                            className="list-disc text-sm leading-relaxed text-foreground-muted"
                          >
                            {o}
                          </li>
                        ))}
                      </ul>
                    </AccordionContent>
                  </AccordionItem>
                  <AccordionItem value="risks">
                    <AccordionTrigger className="text-sm font-semibold hover:no-underline">
                      <span className="text-error">●</span> 风险点（
                      {selected.risks.length}）
                    </AccordionTrigger>
                    <AccordionContent>
                      <ul className="space-y-2 pb-3 pl-4">
                        {selected.risks.map((r, i) => (
                          <li
                            key={i}
                            className="list-disc text-sm leading-relaxed text-foreground-muted"
                          >
                            {r}
                          </li>
                        ))}
                      </ul>
                    </AccordionContent>
                  </AccordionItem>
                </Accordion>
              </DrawerBody>

              <DrawerFooter>
                <div className="flex gap-3">
                  <Button
                    className="flex-1 rounded-md bg-accent text-accent-contrast hover:bg-accent-hover"
                    disabled={!onGoListing}
                    onClick={() => {
                      if (selected && onGoListing) {
                        onGoListing(selected);
                        setSelected(null);
                      }
                    }}
                  >
                    用这个商品生成 Listing
                  </Button>
                  <DrawerClose
                    render={
                      <Button
                        variant="secondary"
                        className="rounded-sm bg-background-muted"
                      >
                        关闭
                      </Button>
                    }
                  />
                </div>
              </DrawerFooter>
            </>
          )}
        </DrawerContent>
      </Drawer>
    </div>
  );
}

function exportReport(
  query: string,
  report: SelectionReport | null,
  candidates: ScoredCandidate[],
  decision: string,
) {
  const payload = {
    query,
    decision,
    generated_at: new Date().toISOString(),
    report,
    candidates,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `selection-${Date.now()}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}