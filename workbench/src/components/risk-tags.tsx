/** 风险标签:report/candidate 字段 → 标签集合(纯函数映射,严重度配色)。 */

export const RISK_LEVELS = {
  high: { label: "高风险", color: "#cd3d64" },
  medium: { label: "中风险", color: "#9a6700" },
  low: { label: "低风险", color: "#8898aa" },
} as const;

export type RiskLevel = keyof typeof RISK_LEVELS;

/** 从候选/报告对象提取风险标签(纯函数,可单测)。 */
export function extractRiskTags(source: Record<string, unknown> | null | undefined): {
  level: RiskLevel;
  text: string;
}[] {
  if (!source) return [];
  const tags: { level: RiskLevel; text: string }[] = [];
  if (source.risk_flags && Array.isArray(source.risk_flags)) {
    for (const raw of source.risk_flags) {
      if (typeof raw !== "string") continue;
      const [lvl, ...rest] = raw.split(":");
      const level = (["high", "medium", "low"] as const).includes(lvl as RiskLevel)
        ? (lvl as RiskLevel)
        : "medium";
      tags.push({ level, text: rest.join(":") || lvl });
    }
  }
  if (source.hard_failed === true) {
    tags.push({ level: "high", text: "硬性合规失败" });
  }
  return tags;
}

export function RiskTags({ tags }: { tags: { level: RiskLevel; text: string }[] }) {
  if (!tags.length) return null;
  return (
    <span style={{ display: "inline-flex", gap: 4 }}>
      {tags.map((t, i) => (
        <span
          key={i}
          style={{
            fontSize: 10,
            border: `1px solid ${RISK_LEVELS[t.level].color}`,
            color: RISK_LEVELS[t.level].color,
            borderRadius: 4,
            padding: "1px 6px",
          }}
        >
          {t.text}
        </span>
      ))}
    </span>
  );
}
