import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

/** 渠道字数限值(常量表,测试固化;改限值=改这里+测试)。 */
export const CHAR_LIMITS: Record<string, number> = {
  item_name: 200,
  title_en: 200,
  title_jp: 200,
  title_zh: 200,
  bullet_point: 250,
  bullets_en: 250,
  bullets_jp: 250,
  bullets_zh: 250,
  product_description: 2000,
  description_en: 2000,
  description_jp: 2000,
  description_zh: 2000,
  generic_keyword: 250,
};

export function limitFor(field: string): number {
  return CHAR_LIMITS[field] ?? 2000;
}

/** 可编辑字段白名单(镜像 server.py _LISTING_FIELD_WHITELIST,改一处须同步另一处)。 */
export const EDITABLE_FIELDS: ReadonlySet<string> = new Set([
  "item_name", "bullet_point", "product_description", "generic_keyword",
  "title_en", "bullets_en", "description_en", "title_jp", "bullets_jp",
  "description_jp", "image_url",
]);

/** 与初始值 diff:只回传用户改过的字段(服务端白名单外字段本来就会 400)。 */
export function diffFields(
  initial: Record<string, unknown>,
  draft: Record<string, unknown>,
): Record<string, unknown> {
  const changed: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(draft)) {
    if (JSON.stringify(initial[k]) !== JSON.stringify(v)) changed[k] = v;
  }
  return changed;
}

/** 语言分组:按字段名后缀(_en/_jp/_zh)分 Tab;无后缀字段进"默认"。 */
export function groupByLanguage(listing: Record<string, unknown>) {
  const groups: Record<string, Record<string, unknown>> = { 默认: {} };
  for (const [k, v] of Object.entries(listing)) {
    if (typeof v !== "string" && !Array.isArray(v)) continue;
    const m = k.match(/_(en|jp|zh)$/);
    if (m) {
      const lang = { en: "English", jp: "日本語", zh: "中文" }[m[1]] ?? m[1];
      (groups[lang] ??= {})[k] = v;
    } else {
      (groups["默认"] ??= {})[k] = v;
    }
  }
  return groups;
}

async function saveFields(runId: string, fields: Record<string, unknown>) {
  const res = await fetch(`/api/listing/runs/${runId}/fields`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ fields }),
  });
  if (!res.ok) throw new Error(`save failed: ${res.status}`);
}

/** 单字段编辑行:限值校验(超限红边+计数)。 */
function FieldRow({
  name,
  value,
  onChange,
}: {
  name: string;
  value: string | string[];
  onChange: (v: string | string[]) => void;
}) {
  const limit = limitFor(name);
  if (Array.isArray(value)) {
    return (
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 12, color: "#6b7c93" }}>
          {name}(每条 ≤{limit})
        </div>
        {value.map((b, i) => (
          <div key={i} style={{ display: "flex", gap: 6, marginTop: 4 }}>
            <input
              value={b}
              onChange={(e) => {
                const next = [...value];
                next[i] = e.target.value;
                onChange(next);
              }}
              style={{
                flex: 1,
                border: `1px solid ${b.length > limit ? "#cd3d64" : "#cfd7df"}`,
                borderRadius: 4,
                padding: "4px 8px",
                fontSize: 13,
              }}
            />
            <span style={{ fontSize: 11, color: b.length > limit ? "#cd3d64" : "#8898aa" }}>
              {b.length}/{limit}
            </span>
          </div>
        ))}
      </div>
    );
  }
  const over = value.length > limit;
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 12, color: "#6b7c93" }}>
        {name} {value.length}/{limit}
        {over ? " 超限" : ""}
      </div>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: "100%",
          border: `1px solid ${over ? "#cd3d64" : "#cfd7df"}`,
          borderRadius: 4,
          padding: "4px 8px",
          fontSize: 13,
        }}
      />
    </div>
  );
}

/**
 * Listing 编辑器(feature-wave-1):多语言 Tab + 字段限值校验 + 人工直写
 * (PATCH /listing/runs/{id}/fields,白名单由服务端把关)。纯自建组件,
 * 不引 antd(双风格边界)。
 */
export default function ListingEditor({
  runId,
  listing,
  onSaved,
}: {
  runId: string;
  listing: Record<string, unknown>;
  /** 保存成功后回传实际写入的字段(聊天台经此同步本地 overrides;console 不传)。 */
  onSaved?: (fields: Record<string, unknown>) => void;
}) {
  const qc = useQueryClient();
  // 只进白名单字段:白名单外(如 product_type)服务端必 400,不渲染不提交
  const initial = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(listing).filter(([k]) => EDITABLE_FIELDS.has(k)),
      ),
    [listing],
  );
  const [draft, setDraft] = useState<Record<string, unknown>>(initial);
  const [tab, setTab] = useState("默认");
  const [saved, setSaved] = useState(false);
  const groups = useMemo(() => groupByLanguage(draft), [draft]);
  const keys = Object.keys(groups);
  const changed = useMemo(() => diffFields(initial, draft), [initial, draft]);
  const hasChanges = Object.keys(changed).length > 0;

  const overLimit = Object.entries(draft).some(([k, v]) => {
    const limit = limitFor(k);
    if (Array.isArray(v)) return v.some((b: string) => b.length > limit);
    return typeof v === "string" && v.length > limit;
  });

  const saveMut = useMutation({
    mutationFn: () => saveFields(runId, changed),
    onSuccess: () => {
      setSaved(true);
      onSaved?.(changed);
      qc.invalidateQueries({ queryKey: ["listing-run", runId] });
      window.setTimeout(() => setSaved(false), 2000);
    },
  });

  const current = groups[tab] ?? {};

  return (
    <div>
      <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
        {keys.map((k) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            style={{
              border: "1px solid #cfd7df",
              background: tab === k ? "#eef0fe" : "#fff",
              color: "#32325d",
              borderRadius: 4,
              padding: "3px 10px",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            {k}
          </button>
        ))}
      </div>
      {Object.entries(current).map(([k, v]) => (
        <FieldRow
          key={k}
          name={k}
          value={v as string | string[]}
          onChange={(nv) => setDraft((d) => ({ ...d, [k]: nv }))}
        />
      ))}
      <button
        onClick={() => saveMut.mutate()}
        disabled={!hasChanges || overLimit || saveMut.isPending}
        style={{
          marginTop: 8,
          background: hasChanges && !overLimit ? "#6772e5" : "#8898aa",
          color: "#fff",
          border: "none",
          borderRadius: 4,
          padding: "6px 14px",
          fontSize: 13,
          cursor: hasChanges && !overLimit ? "pointer" : "not-allowed",
        }}
      >
        {saveMut.isPending ? "保存中…" : hasChanges ? `保存修改（${Object.keys(changed).length} 项）` : "无修改"}
      </button>
      {saved && (
        <span style={{ marginLeft: 8, fontSize: 12, color: "#1b7a4e" }}>已保存 ✓</span>
      )}
    </div>
  );
}
