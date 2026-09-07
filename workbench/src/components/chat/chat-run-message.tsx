"use client";

import { useEffect, useState } from "react";

import type { ProductCandidate, ScoredCandidate } from "@/lib/selection-types";
import type { ListingWireEvent } from "@/lib/listing-types";
import type { ListingCard, SelectionCard } from "@/lib/chat-types";
import { ResultView } from "@/components/views/result-view";
import { ListingRunMessage } from "./listing-run-message";
import { Markdown } from "./markdown";
import { RunTimeline } from "./run-timeline";
import { IconChevronRight, IconLogo, IconX } from "@/components/icons";
import {
  applyListingEvent,
  newListingRun,
  type ListingRunState,
} from "./listing-run-state";
import { chatToolTrace, type ChatRunState } from "./chat-run-state";

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

/** Fold a listing card payload back into a ListingRunState so the existing
 *  ListingRunMessage (export/upload/regen affordances included) renders it. */
function listingRunFromCard(card: ListingCard): ListingRunState {
  let run = newListingRun(card.candidate as ProductCandidate, "", []);
  run = applyListingEvent(run, {
    event: "listing_draft",
    listing: card.listing,
    issues: card.issues,
    hard_failed: card.hard_failed,
  } as unknown as ListingWireEvent);
  run = applyListingEvent(run, {
    event: "final",
    kind: "listing",
    run_id: card.run_id,
    product_id: card.product_id,
    market_code: "US",
    listing: card.listing,
    issues: card.issues,
    hard_failed: card.hard_failed,
    session_id: "",
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any);
  return run;
}

interface ChatRunMessageProps {
  run: ChatRunState;
  onGoConfig: () => void;
  onGoListing?: (candidate: ScoredCandidate) => void;
}

/** General-agent turn — streamed text, tool trajectory, and domain cards. */
export function ChatRunMessage({
  run,
  onGoConfig,
  onGoListing,
}: ChatRunMessageProps) {
  const running = run.phase === "running";
  const elapsed = useElapsed(run.startedAt, running);
  const trace = chatToolTrace(run);

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
        {running && (
          <p className="text-sm text-foreground-muted">
            思考中
            <span className="ml-2 tabular-nums text-foreground-subtle">
              {elapsed}s
            </span>
          </p>
        )}

        {/* Tool trajectory — collapsed by default (codex-style live surface) */}
        {trace.length > 0 && (
          <details className="group" open={running}>
            <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs text-foreground-subtle transition-colors hover:text-foreground-muted">
              <span className="flex transition-transform group-open:rotate-90">
                <IconChevronRight size={12} />
              </span>
              执行过程 · {trace.length} 次工具调用
            </summary>
            <div className="mt-2">
              <RunTimeline calls={trace} running={running} />
            </div>
          </details>
        )}

        {/* Streamed / final assistant text */}
        {run.text.length > 0 && (
          <div className="rounded-xl border border-border-muted bg-card px-4 py-3 text-[15px] leading-relaxed">
            <Markdown text={run.text} />
          </div>
        )}

        {/* Domain cards — selection reports / listing drafts from tools */}
        {run.cards.map((card, i) =>
          card.type === "selection" ? (
            <SelectionCardView
              key={`${card.run_id}-${i}`}
              card={card}
              query={run.query}
              onGoListing={onGoListing}
            />
          ) : (
            <ListingCardView key={`${card.run_id}-${i}`} card={card} onGoConfig={onGoConfig} />
          ),
        )}

        {run.phase === "error" && run.errorInfo && (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-xl border border-error/30 bg-error/5 px-4 py-3 text-sm text-foreground-muted"
          >
            <IconX width={16} height={16} className="mt-0.5 shrink-0 text-error" />
            <div className="min-w-0">
              <p className="font-medium text-foreground">
                请求失败（{run.errorInfo.code}）
              </p>
              <p className="mt-0.5 break-words text-[13px]">
                {run.errorInfo.message}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function SelectionCardView({
  card,
  query,
  onGoListing,
}: {
  card: SelectionCard;
  query: string;
  onGoListing?: (candidate: ScoredCandidate) => void;
}) {
  const seedKeyword = card.report?.seed_keyword || query;
  return (
    <ResultView
      query={seedKeyword}
      report={card.report}
      candidates={card.candidates as ScoredCandidate[]}
      decision={card.decision}
      onGoListing={onGoListing}
    />
  );
}

function ListingCardView({
  card,
  onGoConfig,
}: {
  card: ListingCard;
  onGoConfig: () => void;
}) {
  return <ListingRunMessage run={listingRunFromCard(card)} onGoConfig={onGoConfig} />;
}
