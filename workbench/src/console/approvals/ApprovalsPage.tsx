import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { List, Button, Drawer, Empty, message } from "antd";

interface Approval {
  run_id: string;
  session_id: string;
  kind: string;
  summary: string | null;
  status: string;
  created_at: string;
}

async function fetchApprovals(status: string): Promise<Approval[]> {
  const res = await fetch(`/api/runs/approvals?status=${status}`);
  if (!res.ok) return [];
  return res.json();
}

async function resolve(runId: string, decision: string) {
  const res = await fetch(`/api/runs/${runId}/approval`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ decision }),
  });
  if (!res.ok) throw new Error("resolve failed");
}

export default function ApprovalsPage() {
  const qc = useQueryClient();
  const [detail, setDetail] = useState<Approval | null>(null);
  const { data: pending = [] } = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => fetchApprovals("pending"),
    refetchInterval: 3000,
  });

  const resolveMut = useMutation({
    mutationFn: ({ runId, decision }: { runId: string; decision: string }) =>
      resolve(runId, decision),
    onSuccess: () => {
      message.success("已决议");
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["pipeline"] });
    },
  });

  return (
    <div>
      <h3 style={{ fontWeight: 500, marginBottom: 16 }}>AI 审批</h3>
      {pending.length === 0 ? (
        <Empty description="暂无待审批任务(需 AGENT_DURABLE=on + AGENT_APPROVAL=on)" />
      ) : (
        <List
          dataSource={pending}
          renderItem={(a) => (
            <List.Item
              actions={[
                <Button
                  key="approve"
                  type="primary"
                  size="small"
                  onClick={() => {
                    resolveMut.mutate({ runId: a.run_id, decision: "approve" });
                    setDetail(null);
                  }}
                >
                  通过
                </Button>,
                <Button
                  key="reject"
                  size="small"
                  danger
                  onClick={() => {
                    resolveMut.mutate({ runId: a.run_id, decision: "reject" });
                    setDetail(null);
                  }}
                >
                  拒绝
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={`${a.kind} · ${a.run_id.slice(0, 8)}`}
                description={a.summary ?? ""}
              />
            </List.Item>
          )}
        />
      )}
      <Drawer
        title="审批详情"
        open={detail !== null}
        onClose={() => setDetail(null)}
        width={480}
      >
        {detail ? (
          <>
            <p>
              <b>run:</b> {detail.run_id}
            </p>
            <p>
              <b>session:</b> {detail.session_id}
            </p>
            <p>
              <b>摘要:</b>
            </p>
            <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
              {detail.summary}
            </pre>
          </>
        ) : null}
      </Drawer>
    </div>
  );
}
