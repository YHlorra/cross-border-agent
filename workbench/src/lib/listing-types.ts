// mirror src/agent/listing/schema.py (Pydantic) field-for-field. Keep the
// two in lock-step — the ListingOutput field names ARE the SP-API
// `attributes` keys, so this type doubles as the API payload shape
// (: tier-1 serialize and tier-2 render share one schema).

export interface ListingOutput {
  item_name: string;
  bullet_point: string[];
  product_description: string;
  generic_keyword: string;
  product_type?: string | null;
  subject_matter?: string | null;
  target_audience?: string | null;
}

export interface ListingIssue {
  field: string;
  code: string;
  message: string;
  severity: "auto_fixed" | "hard_fail";
}

/** 可变商业参数 — 标准文件模板的可填变量（价格从候选带出，可改）。 */
export interface ListingFacts {
  price_usd: number;
  quantity: number;
  sku: string;
  brand: string;
  condition: string;
  fulfillment_channel: string;
}

/** NDJSON wire events from POST /api/listing/run. */
export type ListingWireEvent =
  | { event: "node_start"; node: string }
  | {
      event: "node_end";
      node: string;
      summary: string;
      duration_ms: number;
    }
  | { event: "listing_chunk"; delta: string }
  | {
      event: "listing_draft";
      listing: ListingOutput;
      issues: ListingIssue[];
      hard_failed: boolean;
    }
  | {
      event: "final";
      kind: "listing";
      run_id: string;
      product_id: string;
      market_code: string;
      listing: ListingOutput;
      issues: ListingIssue[];
      hard_failed: boolean;
      session_id: string;
    }
  | {
      event: "error";
      code: string;
      message: string;
      status?: number | null;
      body?: unknown;
      headers?: unknown;
    };

/** Per-market format limits (mirror data/selection/marketplaces.json US). */
export const LISTING_FORMAT = {
  titleMaxChars: 75,
  bulletCount: 5,
  bulletMinChars: 10,
  bulletMaxChars: 255,
  descriptionMaxChars: 2000,
  searchTermsMaxBytes: 250,
} as const;

export function byteLength(s: string): number {
  return new TextEncoder().encode(s).length;
}
