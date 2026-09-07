import { useQueries } from "@tanstack/react-query";
import { fetchHistory } from "@/lib/selection-api";
import { fetchListingRuns as fetchDrafts } from "@/lib/listing-api";

type ColumnKey =
  | "running"
  | "queued"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled";

const COLUMNS: { key: ColumnKey; label: string }[] = [
  { key: "running", label: "运行中" },
  { key: "queued", label: "排队" },
  { key: "awaiting_approval", label: "待审批" },
  { key: "completed", label: "已完成" },
  { key: "failed", label: "失败" },
  { key: "cancelled", label: "已取消" },
];

interface Card {
  run_id: string;
  title: string;
  status: ColumnKey;
}

/** 三源归一(selection history + listing drafts + approvals)→ 看板卡片。 */
export function usePipelineBoard() {
  const results = useQueries({
    queries: [
      {
        queryKey: ["pipeline-history"],
        queryFn: () => fetchHistory(),
        refetchInterval: 4000,
      },
      {
        queryKey: ["pipeline-drafts"],
        queryFn: () => fetchDrafts(),
        refetchInterval: 4000,
      },
      {
        queryKey: ["pipeline-approvals"],
        queryFn: async () => {
          const res = await fetch("/api/runs/approvals?status=pending");
          return res.ok ? ((await res.json()) as any[]) : [];
        },
        refetchInterval: 4000,
      },
    ],
  });

  const history = (results[0].data ?? []) as any[];
  const drafts = (results[1].data ?? []) as any[];
  const approvals = (results[2].data ?? []) as any[];

  const cards: Card[] = [
    ...history.map((r: any) => ({
      run_id: r.id,
      title: r.query || r.seed_keyword || r.id,
      status: (r.answer_failed ? "failed" : "completed") as ColumnKey,
    })),
    ...drafts.map((d: any) => ({
      run_id: d.id,
      title: d.title || d.listing_title || d.product_name || d.id,
      status: (d.hard_failed ? "failed" : "completed") as ColumnKey,
    })),
    ...approvals.map((a: any) => ({
      run_id: a.run_id,
      title: a.summary || a.run_id,
      status: "awaiting_approval" as ColumnKey,
    })),
  ];
  const loading = results.some((r) => r.isLoading);
  return { cards, loading };
}

export function PipelinePage() {
  const { cards } = usePipelineBoard();
  return (
    <div>
      <h3 style={{ fontWeight: 500, marginBottom: 16 }}>流水线看板</h3>
      <div style={{ display: "flex", gap: 12, overflowX: "auto" }}>
        {COLUMNS.map((col) => (
          <div
            key={col.key}
            style={{
              flex: "0 0 220px",
              background: "#f6f9fc",
              borderRadius: 8,
              padding: 12,
              minHeight: 120,
            }}
          >
            <div style={{ fontSize: 12, color: "#6b7c93", marginBottom: 8 }}>
              {col.label}({cards.filter((c) => c.status === col.key).length})
            </div>
            {cards
              .filter((c) => c.status === col.key)
              .map((c) => (
                <div
                  key={c.run_id}
                  style={{
                    background: "#fff",
                    border: "1px solid #e0e5f0",
                    borderRadius: 6,
                    padding: 8,
                    marginBottom: 8,
                    fontSize: 13,
                  }}
                >
                  {c.title}
                </div>
              ))}
          </div>
        ))}
      </div>
    </div>
  );
}
