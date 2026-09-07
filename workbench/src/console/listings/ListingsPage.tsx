import { useState } from "react";
import { ProTable, type ProColumns } from "@ant-design/pro-components";
import { Button, Popconfirm, message } from "antd";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Drawer } from "antd";
import ListingEditor from "@/components/listing/editor";
import {
  fetchListingRuns,
  fetchListingRun,
  renameListingRun,
  deleteListingRun,
} from "@/lib/listing-api";

interface Row {
  id: string;
  title: string | null;
  listing_title: string;
  product_name: string;
  market_code: string;
  created_at: string;
}

export default function ListingsPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Row | null>(null);
  const [editorFor, setEditorFor] = useState<{ id: string; listing: Record<string, unknown> } | null>(null);
  const detailQuery = useQuery({
    queryKey: ["listing-detail", editorFor?.id],
    enabled: editorFor !== null,
    queryFn: () => fetchListingRun(editorFor!.id),
  });
  const { data = [], isLoading } = useQuery({
    queryKey: ["listing-runs"],
    queryFn: () => fetchListingRuns(),
  });

  const renameMut = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      renameListingRun(id, title),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["listing-runs"] }),
  });
  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteListingRun(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["listing-runs"] }),
  });

  const columns: ProColumns<Row>[] = [
    {
      title: "标题",
      dataIndex: "title",
      render: (_, r) => r.title ?? r.listing_title ?? r.product_name,
    },
    { title: "渠道", dataIndex: "market_code", width: 80 },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 180,
      render: (_, r) => r.created_at?.replace("T", " ").slice(0, 19),
    },
    {
      title: "操作",
      valueType: "option",
      width: 200,
      render: (_, r) => [
        <a
          key="edit"
          onClick={async () => {
            const full = await fetchListingRun(r.id);
            setEditorFor({ id: r.id, listing: (full?.listing as Record<string, unknown>) ?? {} });
          }}
        >
          编辑
        </a>,
        <a
          key="rename"
          onClick={() => {
            const t = window.prompt("新标题", r.title ?? "");
            if (t) renameMut.mutate({ id: r.id, title: t });
          }}
        >
          重命名
        </a>,
        <Popconfirm
          key="del"
          title="确认删除该草稿?"
          onConfirm={() => {
            deleteMut.mutate(r.id);
            message.success("已删除");
          }}
        >
          <a style={{ color: "#cd3d64" }}>删除</a>
        </Popconfirm>,
      ],
    },
  ];

  return (
    <div>
    <ProTable<Row>
      headerTitle="Listing 草稿"
      rowKey="id"
      columns={columns}
      search={false}
      loading={isLoading}
      dataSource={data as Row[]}
      pagination={{ pageSize: 10 }}
      options={{ reload: true }}
    />
      <Drawer
        title="Listing 编辑器"
        open={editorFor !== null}
        onClose={() => setEditorFor(null)}
        width={560}
      >
        {editorFor && detailQuery.data?.listing ? (
          <ListingEditor
            runId={editorFor.id}
            listing={detailQuery.data.listing as Record<string, unknown>}
          />
        ) : (
          <div style={{ color: "#8898aa", fontSize: 13 }}>加载中…</div>
        )}
      </Drawer>
    </div>
  );
}

export async function openListingDetail(id: string) {
  return fetchListingRun(id);
}
