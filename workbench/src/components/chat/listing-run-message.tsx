"use client";

import { useEffect, useState } from "react";
import { Badge } from "@appica/ui-react/badge";
import { Card } from "@appica/ui-react/card";
import { Button } from "@appica/ui-react/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@appica/ui-react/dialog";
import { Input } from "@appica/ui-react/input";

import type { ProductCandidate, ScoredCandidate } from "@/lib/selection-types";
import {
  LISTING_FORMAT,
  byteLength,
  type ListingIssue,
  type ListingOutput,
} from "@/lib/listing-types";
import {
  deleteListingRun,
  exportListing,
  fetchListingChannel,
  regenListingField,
  renameListingRun,
  uploadListing,
} from "@/lib/listing-api";
import { ErrorView } from "@/components/views/error-view";
import ListingEditor from "@/components/listing/editor";
import type { ListingRunState } from "./listing-run-state";
import {
  IconCheck,
  IconDots,
  IconMenu,
  IconX,
  PencilLineIcon,
  TrashIcon,
} from "@/components/icons";

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

interface ListingRunMessageProps {
  run: ListingRunState;
  onGoConfig: () => void;
}

/** Agent turn for one listing run — mirrors AgentRunMessage's lifecycle. */
export function ListingRunMessage({ run, onGoConfig }: ListingRunMessageProps) {
  const running = run.phase === "running";
  const elapsed = useElapsed(run.startedAt, running);
  const candidate: ProductCandidate = run.candidate;

  return (
    <div className="flex gap-3 stagger-in">
      <div
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-sm ${
          running ? "" : "bg-primary-soft text-accent"
        }`}
        aria-hidden
      >
        {running ? <span className="think-ring" /> : <IconMenu width={18} height={18} />}
      </div>

      <div className="min-w-0 flex-1 space-y-3">
        {running && (
          <p className="text-sm text-foreground-muted">
            正在生成 Listing
            {run.streamedDraft.length > 0
              ? ` · 已输出 ${run.streamedDraft.length} 字符`
              : ""}
            …
            <span className="ml-2 tabular-nums text-foreground-subtle">
              {elapsed}s
            </span>
          </p>
        )}

        {run.phase === "final" && run.draft && (
          <ListingDraftCard run={run} draft={run.draft} issues={run.issues} />
        )}

        {run.phase === "error" && run.errorInfo && (
          <ErrorView
            query={`Listing · ${candidate.name_en || candidate.name_cn}`}
            code={run.errorInfo.code}
            message={run.errorInfo.message}
            status={run.errorInfo.status}
            onRetry={() => {}}
            onBack={() => {}}
            onGoConfig={onGoConfig}
          />
        )}

        {run.phase === "cancelled" && (
          <p className="text-xs text-foreground-subtle">
            已停止 Listing 生成（{elapsed}s）
          </p>
        )}
      </div>
    </div>
  );
}

// ──── Draft card: field sections + live counts + issues + facts form ────────

function ListingDraftCard({
  run,
  draft,
  issues,
}: {
  run: ListingRunState;
  draft: ListingOutput;
  issues: ListingIssue[];
}) {
  const [productType, setProductType] = useState(draft.product_type ?? "");
  // local overrides on top of the prop draft so single-field regen
  // can patch without going through the run-state fold. The prop `draft`
  // is the initial value; once any regen fires we own the slice locally.
  const [overrides, setOverrides] = useState<Partial<ListingOutput>>({});
  const [issueOverrides, setIssueOverrides] = useState<ListingIssue[] | null>(null);
  const [regenLoading, setRegenLoading] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renamingText, setRenamingText] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const live: ListingOutput = { ...draft, ...overrides };
  const liveIssues = issueOverrides ?? issues;

  const onRegen = async (fieldName: keyof ListingOutput) => {
    if (!run.runId) return;
    // wave1 互斥：编辑器打开时 regen 一律拒绝，避免两路写同字段互相覆盖
    if (editing) return;
    setRegenLoading(fieldName);
    try {
      const res = await regenListingField(run.runId, fieldName);
      setOverrides((prev) => ({ ...prev, [fieldName]: res.value }));
      setIssueOverrides(res.issues);
    } catch (e) {
      // C5 (3.6) — debugging residue: console.error + alert are loud and
      // synchronous. Surface the failure through the chat stream (system
      // entry kind added in G3) so the message is persistent, dismissable
      // like every other agent turn, and matches the rest of the wire.
      // The caller (Workbench) owns the system-entry insertion via the
      // onRegenError callback; here we just log + rethrow.
      console.error("regen failed", e);
      throw e;
    } finally {
      setRegenLoading(null);
    }
  };

  const onRenameSubmit = async () => {
    if (!run.runId) return;
    const t = renamingText.trim();
    if (t === "") {
      setRenaming(false);
      return;
    }
    try {
      await renameListingRun(run.runId, t);
      setOverrides((prev) => ({ ...prev, item_name: live.item_name }));
      setRenaming(false);
      setMenuOpen(false);
    } catch (e) {
      throw e;
    }
  };

  const onDeleteConfirm = async () => {
    if (!run.runId) return;
    try {
      await deleteListingRun(run.runId);
      setDeleting(false);
      setMenuOpen(false);
      // Mark the run as deleted in parent state via a system entry is the
      // caller's job (workbench.tsx owns the run-state fold). For now we
      // rely on the listing 列表 page surface; chat-side collapse is a
      // soft fade via a state flag the caller can read on next render.
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Card frame={false} className="bg-card p-5 [--card-radius:var(--radius-md)]">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-base font-semibold">
            Listing 草稿 · {run.candidate.name_en || run.candidate.name_cn}
          </h3>
          <p className="text-xs text-foreground-subtle">Amazon US · 英文</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {run.hardFailed || liveIssues.some((i) => i.severity === "hard_fail") ? (
            <Badge variant="error" className="rounded-full">
              需修改
            </Badge>
          ) : (
            <Badge variant="success" className="rounded-full">
              格式合规
            </Badge>
          )}
          {run.runId && (
            <div className="relative">
              <button
                type="button"
                onClick={() => setMenuOpen((o) => !o)}
                aria-label="操作"
                className="flex h-7 w-7 items-center justify-center rounded-md text-foreground-subtle transition-colors hover:bg-background-muted hover:text-foreground"
              >
                <IconDots size={16} />
              </button>
              {menuOpen && (
                <>
                  <button
                    type="button"
                    aria-hidden
                    onClick={() => setMenuOpen(false)}
                    className="fixed inset-0 z-40 cursor-default"
                  />
                  <div
                    data-listing-menu
                    className="absolute right-0 top-9 z-50 w-44 overflow-hidden rounded-lg border border-border-muted bg-card shadow-card"
                  >
                    <ul className="py-1 text-sm">
                      <li>
                        <button
                          type="button"
                          disabled={!!regenLoading}
                          onClick={() => {
                            setEditing(true);
                            setMenuOpen(false);
                          }}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-background-muted disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <PencilLineIcon size={14} />
                          编辑草稿
                        </button>
                      </li>
                      <li>
                        <button
                          type="button"
                          onClick={() => {
                            setRenamingText(run.title ?? run.candidate.name_en ?? "");
                            setRenaming(true);
                            setMenuOpen(false);
                          }}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-background-muted"
                        >
                          <PencilLineIcon size={14} />
                          重命名
                        </button>
                      </li>
                      <li>
                        <button
                          type="button"
                          onClick={() => {
                            setDeleteError(null);
                            setDeleting(true);
                            setMenuOpen(false);
                          }}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left text-error transition-colors hover:bg-error/10"
                        >
                          <TrashIcon size={14} />
                          删除草稿
                        </button>
                      </li>
                    </ul>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {liveIssues.length > 0 && (
        <div className="mb-4 space-y-1.5 rounded-lg bg-background-muted p-3">
          {liveIssues.map((iss, i) => (
            <p
              key={i}
              className={`text-xs leading-relaxed ${
                iss.severity === "hard_fail"
                  ? "text-error"
                  : "text-foreground-muted"
              }`}
            >
              {iss.severity === "hard_fail" ? <IconX className="inline align-[-2px]" /> : <span className="inline-flex items-center gap-0.5"><IconCheck className="align-[-2px]" /> 已自动修复</span>} ·{" "}
              {iss.message}
            </p>
          ))}
        </div>
      )}

      <div className="space-y-4">
        <FieldSection
          label="标题"
          count={`${live.item_name.length}/${LISTING_FORMAT.titleMaxChars}`}
          text={live.item_name}
          fieldName="item_name"
          onRegen={run.runId && !editing ? () => void onRegen("item_name") : undefined}
          regenLoading={regenLoading === "item_name"}
        />
        <FieldSection
          label="五点描述"
          count={`${live.bullet_point.length} 条 · 每条 ≤${LISTING_FORMAT.bulletMaxChars}`}
          text={live.bullet_point.map((b, i) => `${i + 1}. ${b}`).join("\n")}
          multiline
          fieldName="bullet_point"
          onRegen={run.runId && !editing ? () => void onRegen("bullet_point") : undefined}
          regenLoading={regenLoading === "bullet_point"}
        />
        <FieldSection
          label="商品描述"
          count={`${live.product_description.length}/${LISTING_FORMAT.descriptionMaxChars}`}
          text={live.product_description}
          multiline
          fieldName="product_description"
          onRegen={run.runId && !editing ? () => void onRegen("product_description") : undefined}
          regenLoading={regenLoading === "product_description"}
        />
        <FieldSection
          label="后台搜索词"
          count={`${byteLength(live.generic_keyword)}/${LISTING_FORMAT.searchTermsMaxBytes} 字节`}
          text={live.generic_keyword}
          fieldName="generic_keyword"
          onRegen={run.runId && !editing ? () => void onRegen("generic_keyword") : undefined}
          regenLoading={regenLoading === "generic_keyword"}
        />
        {live.product_type && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-foreground-subtle">产品类型</span>
            <ProductTypeInput
              value={productType}
              onChange={setProductType}
            />
            <RegenButton
              loading={regenLoading === "product_type"}
              disabled={editing}
              onClick={() => void onRegen("product_type")}
            />
            <span className="text-xs text-foreground-subtle">
              （LLM 提议，上传前可改）
            </span>
          </div>
        )}
      </div>

      <FactsForm
        runId={run.runId}
        candidate={run.candidate}
        sellerBrand={run.brand}
        productType={productType}
      />

      {editing && run.runId && (
        <div className="mt-4 border-t border-border-muted pt-4">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-foreground-muted">
              编辑草稿（保存后写回草稿，重生成已暂停）
            </span>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="text-[11px] text-foreground-subtle transition-colors hover:text-foreground"
            >
              收起
            </button>
          </div>
          <ListingEditor
            runId={run.runId}
            listing={live as unknown as Record<string, unknown>}
            onSaved={(fields) => {
              setOverrides((prev) => ({ ...prev, ...fields }));
              setEditing(false);
            }}
          />
        </div>
      )}

      {renaming && (
        <Dialog open onOpenChange={(o) => !o && setRenaming(false)}>
          <DialogContent className="max-w-sm">
            <DialogHeader>
              <DialogTitle>重命名 Listing 草稿</DialogTitle>
              <DialogDescription>
                留空将恢复默认（候选商品名称）。
              </DialogDescription>
            </DialogHeader>
            <Input
              autoFocus
              value={renamingText}
              onChange={(e) => setRenamingText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void onRenameSubmit();
                }
              }}
            />
            <DialogFooter>
              <Button variant="outline" onClick={() => setRenaming(false)}>
                取消
              </Button>
              <Button onClick={() => void onRenameSubmit()}>保存</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      {deleting && (
        <Dialog open onOpenChange={(o) => !o && !deleteError && setDeleting(false)}>
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle className="text-error">删除 Listing 草稿？</DialogTitle>
              <DialogDescription>
                将永久删除该草稿。商品本身仍在选品结果中，可重新生成。
                <br />
                <span className="mt-2 inline-block font-medium text-error">
                  此操作不可撤销。
                </span>
              </DialogDescription>
            </DialogHeader>
            {deleteError && (
              <p className="text-xs text-error">删除失败：{deleteError}</p>
            )}
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => {
                  setDeleting(false);
                  setDeleteError(null);
                }}
              >
                取消
              </Button>
              <Button
                className="bg-error text-primary-foreground hover:bg-error/90"
                onClick={() => void onDeleteConfirm()}
              >
                确认删除
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </Card>
  );
}

function FieldSection({
  label,
  count,
  text,
  multiline = false,
  fieldName,
  onRegen,
  regenLoading,
}: {
  label: string;
  count: string;
  text: string;
  multiline?: boolean;
  fieldName?: keyof ListingOutput;
  onRegen?: () => void;
  regenLoading?: boolean;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-xs font-semibold text-foreground-muted">
          {label}
        </span>
        <span className="flex items-center gap-2">
          <span className="tabular-nums text-[11px] text-foreground-subtle">
            {count}
          </span>
          <CopyButton text={text} />
          {fieldName && onRegen && (
            <RegenButton loading={!!regenLoading} onClick={onRegen} />
          )}
        </span>
      </div>
      <div className="rounded-lg bg-background-muted p-3">
        {multiline ? (
          <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-foreground">
            {text}
          </pre>
        ) : (
          <p className="text-sm leading-relaxed text-foreground">{text}</p>
        )}
      </div>
    </div>
  );
}

function RegenButton({
  loading,
  onClick,
  disabled = false,
}: {
  loading: boolean;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={loading || disabled}
      title={disabled ? "编辑草稿时暂停重生成" : undefined}
      className="rounded-md bg-background-muted px-2.5 py-0.5 text-[11px] text-foreground-muted transition-colors hover:bg-background-strong hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
      onClick={onClick}
    >
      {loading ? "重生成中…" : "重生成"}
    </button>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="rounded-md bg-background-muted px-2.5 py-0.5 text-[11px] text-foreground-muted transition-colors hover:bg-background-strong hover:text-foreground"
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? "已复制" : "复制"}
    </button>
  );
}

// ──── ListingFacts 变量表单 + 双通道出口（导出 / 上传，） ────────────

/** product_type：LLM 提议 + 人工放行（编辑后随导出/上传 payload 提交）。 */
function ProductTypeInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const [editing, setEditing] = useState(false);

  if (!editing) {
    return (
      <button
        type="button"
        className="rounded-md bg-primary-soft px-2.5 py-0.5 text-xs font-medium text-accent transition-colors hover:bg-background-muted"
        onClick={() => setEditing(true)}
        title="点击修改"
      >
        {value}
      </button>
    );
  }
  return (
    <input
      autoFocus
      className="h-7 rounded-md border-0 bg-background-muted px-3 text-xs text-foreground outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
      value={value}
      onChange={(e) => onChange(e.target.value.toUpperCase())}
      onBlur={() => setEditing(false)}
      onKeyDown={(e) => e.key === "Enter" && setEditing(false)}
    />
  );
}

function FactsForm({
  runId,
  candidate,
  sellerBrand,
  productType,
}: {
  runId: string | null;
  candidate: ProductCandidate;
  sellerBrand: string;
  productType: string;
}) {
  const [price, setPrice] = useState(String(candidate.target_price_usd ?? ""));
  const [quantity, setQuantity] = useState("1");
  const [sku, setSku] = useState("");
  const [brand, setBrand] = useState(sellerBrand);
  const [channel, setChannel] = useState<"api" | "export">("export");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    void fetchListingChannel().then(setChannel);
  }, []);

  const facts = () => {
    const out: Record<string, string | number> = {};
    if (price.trim()) out.price_usd = Number(price);
    if (quantity.trim()) out.quantity = Number(quantity);
    if (sku.trim()) out.sku = sku.trim();
    if (brand.trim()) out.brand = brand.trim();
    if (productType.trim()) out.product_type = productType.trim();
    return out;
  };

  const doExport = async () => {
    if (!runId) return;
    setBusy(true);
    setFailed(null);
    try {
      const exportData = await exportListing(runId, facts());
      const blob = new Blob([JSON.stringify(exportData, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `listing-${runId.slice(0, 8)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setNote("标准文件已下载：payload 可直接用于 SP-API 上传");
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const doUpload = async () => {
    if (!runId) return;
    setBusy(true);
    setFailed(null);
    setNote(null);
    try {
      const result = await uploadListing(runId, facts());
      setNote(
        `上传完成：上游返回 ${result.status}（结果原样透传，未包装）`,
      );
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const input =
    "h-7 rounded-md border-0 bg-background-muted px-3 text-xs text-foreground outline-none placeholder:text-foreground-subtle focus-visible:ring-2 focus-visible:ring-accent/40";

  return (
    <div className="mt-5 border-t border-border-muted pt-4">
      <p className="mb-2.5 text-xs font-semibold text-foreground-muted">
        商业参数
        <span className="ml-2 font-normal text-foreground-subtle">
          可变项 · 价格已从选品结果带出，可修改
        </span>
      </p>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <label className="flex items-center gap-1.5 text-xs text-foreground-muted">
          价格 $
          <input
            className={`${input} w-20 tabular-nums`}
            value={price}
            inputMode="decimal"
            onChange={(e) => setPrice(e.target.value)}
          />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-foreground-muted">
          数量
          <input
            className={`${input} w-16 tabular-nums`}
            value={quantity}
            inputMode="numeric"
            onChange={(e) => setQuantity(e.target.value)}
          />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-foreground-muted">
          SKU
          <input
            className={`${input} w-28`}
            value={sku}
            placeholder="卖家 SKU"
            onChange={(e) => setSku(e.target.value)}
          />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-foreground-muted">
          品牌
          <input
            className={`${input} w-28`}
            value={brand}
            placeholder="留空 = 无品牌"
            onChange={(e) => setBrand(e.target.value)}
          />
        </label>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            className="h-7 rounded-md bg-background-muted px-3 text-xs hover:bg-background-strong"
            disabled={busy || !runId}
            onClick={() => void doExport()}
          >
            导出标准文件
          </Button>
          {channel === "api" && (
            <Button
              className="h-7 rounded-md bg-accent px-3 text-xs text-accent-contrast hover:bg-accent-hover"
              disabled={busy || !runId || !sku.trim()}
              title={!sku.trim() ? "上传需要 SKU" : undefined}
              onClick={() => void doUpload()}
            >
              上传 Amazon
            </Button>
          )}
        </div>
      </div>
      {channel === "export" && (
        <p className="mt-2 text-[11px] text-foreground-subtle">
          未配置 SP-API 凭据 — 已退化到导出通道（配置页可启用自动上传）
        </p>
      )}
      {note && <p className="mt-2 text-xs text-accent">{note}</p>}
      {failed && <p className="mt-2 text-xs text-error">{failed}</p>}
    </div>
  );
}
