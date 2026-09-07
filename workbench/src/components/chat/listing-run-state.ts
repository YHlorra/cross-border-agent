// Pure fold for one listing run's NDJSON stream — mirrors run-state.ts
// (single-node pipeline: progress is the raw draft stream accumulation).
import type { ProductCandidate } from "@/lib/selection-types";
import type {
  ListingIssue,
  ListingOutput,
  ListingWireEvent,
} from "@/lib/listing-types";
import type { ErrorInfo } from "./run-state";

export type ListingRunPhase = "running" | "final" | "error" | "cancelled";

export interface ListingRunState {
  candidate: ProductCandidate;
  brand: string;
  competitorBrands: string[];
  phase: ListingRunPhase;
  startedAt: number;
  /** raw think-filtered stream — progress surface only. */
  streamedDraft: string;
  draft: ListingOutput | null;
  issues: ListingIssue[];
  hardFailed: boolean;
  runId: string | null;
  errorInfo: ErrorInfo | null;
  /** user-chosen alias (PATCH /listing/runs/{id}). null while never renamed. */
  title: string | null;
}

export function newListingRun(
  candidate: ProductCandidate,
  brand: string,
  competitorBrands: string[],
): ListingRunState {
  return {
    candidate,
    brand,
    competitorBrands,
    phase: "running",
    startedAt: Date.now(),
    streamedDraft: "",
    draft: null,
    issues: [],
    hardFailed: false,
    runId: null,
    errorInfo: null,
    title: null,
  };
}

export function applyListingEvent(
  run: ListingRunState,
  event: ListingWireEvent,
): ListingRunState {
  switch (event.event) {
    case "listing_chunk":
      return { ...run, streamedDraft: run.streamedDraft + event.delta };
    case "listing_draft":
      return {
        ...run,
        draft: event.listing,
        issues: event.issues,
        hardFailed: event.hard_failed,
      };
    case "final":
      return {
        ...run,
        phase: "final",
        draft: event.listing,
        issues: event.issues,
        hardFailed: event.hard_failed,
        runId: event.run_id,
      };
    case "error":
      return {
        ...run,
        phase: "error",
        errorInfo: {
          code: event.code,
          message: event.message,
          status: event.status ?? null,
        },
      };
    default:
      return run;
  }
}
