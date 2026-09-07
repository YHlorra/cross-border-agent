/** 竞品对比卡:同候选竞品的价格/评分/评论/链接表格化(空数组不渲染)。 */
export interface Competitor {
  name: string;
  price?: number | string;
  rating?: number | string;
  reviews?: number | string;
  url?: string;
}

export default function CompetitorCard({
  candidates,
  title = "竞品对比",
}: {
  candidates: Competitor[];
  title?: string;
}) {
  if (!candidates.length) return null;
  return (
    <div
      style={{
        border: "1px solid #e0e5f0",
        borderRadius: 8,
        padding: 16,
        marginTop: 12,
        background: "#fff",
      }}
    >
      <div style={{ fontSize: 13, fontWeight: 500, color: "#32325d", marginBottom: 8 }}>
        {title}
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
        <thead>
          <tr style={{ color: "#6b7c93", textAlign: "left" }}>
            <th style={td}>竞品</th>
            <th style={td}>价格</th>
            <th style={td}>评分</th>
            <th style={td}>评论数</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c) => (
            <tr key={c.name}>
              <td style={td}>{c.url ? <a href={c.url}>{c.name}</a> : c.name}</td>
              <td style={td}>{c.price ?? "—"}</td>
              <td style={td}>{c.rating ?? "—"}</td>
              <td style={{ ...td, fontVariantNumeric: "tabular-nums" }}>
                {c.reviews ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const td = {
  padding: "6px 8px",
  borderBottom: "1px solid #eef2f7",
} as const;
