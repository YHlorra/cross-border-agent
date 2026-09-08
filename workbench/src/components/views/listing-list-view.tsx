"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { Card } from "@appica/ui-react/card";
import { Skeleton } from "@appica/ui-react/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@appica/ui-react/dialog";
import { Input } from "@appica/ui-react/input";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@appica/ui-react/tooltip";
import {
  Drawer,
  DrawerBody,
  DrawerContent,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from "@appica/ui-react/drawer";

import {
  deleteListingRun,
  fetchListingRun,
  fetchListingRuns,
  renameListingRun,
  type ListingRunDetail,
  type ListingRunSummary,
} from "@/lib/listing-api";
import {
  LISTING_FORMAT,
  byteLength,
  type ListingOutput,
} from "@/lib/listing-types";
import {
  IconDots,
  PencilLineIcon,
  TrashIcon,
} from "@/components/icons";

/** Listing 列表— 侧栏产物视图：已持久化草稿的汇总行 +
 *  只读详情抽屉 + 行内右键/hover 操作（重命名/删除）。
 *  与 chat 卡片 + console 列表同源 API：rename/delete listing run。 */
export function ListingListView() {
  const [runs, setRuns] = useState<ListingRunSummary[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<ListingRunSummary | null>(null);
  const [deleting, setDeleting] = useState<ListingRunSummary | null>(null);
  const [menuFor, setMenuFor] = useState<{
    id: string;
    x: number;
    y: number;
  } | null>(null);

  const refresh = useCallback(async () => {
    setRuns(null);
    setRuns(await fetchListingRuns());
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Click-outside / Escape closes the right-click menu.
  useEffect(() => {
    if (!menuFor) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t?.closest?.("[data-listing-row-menu]")) setMenuFor(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuFor(null);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuFor]);

  const onRename = async (r: ListingRunSummary, newTitle: string): Promise<void> => {
    try {
      await renameListingRun(r.id, newTitle);
      setRenaming(null);
      await refresh();
    } catch (e) {
      console.error("rename listing run failed", e);
    }
  };

  const onDelete = async (r: ListingRunSummary): Promise<void> => {
    try {
      await deleteListingRun(r.id);
      setDeleting(null);
      await refresh();
    } catch (e) {
      console.error("delete listing run failed", e);
    }
  };

  return (
    <div className="mx-auto max-w-4xl px-6 py-8 xl:max-w-5xl">
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground-intense">
            Listing 列表
          </h1>
          <p className="mt-1 text-sm text-foreground-muted">
            历次生成的 Listing 草稿都在这里；点击查看只读详情。
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void refresh()}>
          刷新
        </Button>
      </div>

      {runs === null ? (
        <div className="space-y-2">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : runs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border-secondary p-10 text-center">
          <p className="text-sm text-foreground-muted">还没有 Listing 草稿</p>
          <p className="mt-1 text-xs text-foreground-subtle">
            在对话里运行 /listing，或在选品结果卡上点「转入 Listing 生成」
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {runs.map((r) => (
            <li key={r.id}>
              <Card
                frame={false}
                className="group card-hover border border-border-muted bg-card p-4 [--card-radius:var(--radius-md)]"
              >
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 text-left"
                    onClick={() => setOpenId(r.id)}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      setMenuFor({ id: r.id, x: e.clientX, y: e.clientY });
                    }}
                  >
                    <span
                      className={`h-2 w-2 shrink-0 rounded-sm ${
                        r.hard_failed ? "bg-error" : "bg-accent"
                      }`}
                      title={r.hard_failed ? "存在硬校验失败" : "校验通过"}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-foreground">
                        {r.title || r.listing_title || r.product_name || r.product_id}
                      </span>
                      <span className="block text-[11px] text-foreground-subtle">
                        {formatTime(r.created_at)} · 会话 {r.session_id.slice(0, 8)}…
                      </span>
                    </span>
                    <Badge variant="outline" className="shrink-0 text-[10px]">
                      {r.market_code}
                    </Badge>
                  </button>
                  <TooltipProvider delay={150}>
                    <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <button
                              type="button"
                              aria-label="重命名"
                              onClick={() => setRenaming(r)}
                              className="flex h-7 w-7 items-center justify-center rounded text-foreground-subtle transition-colors hover:bg-background-muted hover:text-foreground [&_svg]:stroke-[1.75]"
                            >
                              <PencilLineIcon size={14} />
                            </button>
                          }
                        />
                        <TooltipContent>重命名</TooltipContent>
                      </Tooltip>
                      <Tooltip>
                        <TooltipTrigger
                          render={
                            <button
                              type="button"
                              aria-label="删除"
                              onClick={() => setDeleting(r)}
                              className="flex h-7 w-7 items-center justify-center rounded text-foreground-subtle transition-colors hover:bg-error/10 hover:text-error [&_svg]:stroke-[1.75]"
                            >
                              <TrashIcon size={14} />
                            </button>
                          }
                        />
                        <TooltipContent>删除草稿（不可撤销）</TooltipContent>
                      </Tooltip>
                    </div>
                  </TooltipProvider>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      <ListingDetailDrawer
        runId={openId}
        onOpenChange={(open) => !open && setOpenId(null)}
      />

      {menuFor && runs && (() => {
        const target = runs.find((x) => x.id === menuFor.id);
        if (!target) return null;
        return (
          <ListingRowContextMenu
            anchor={menuFor}
            onClose={() => setMenuFor(null)}
            onRename={() => {
              setRenaming(target);
              setMenuFor(null);
            }}
            onDelete={() => {
              setDeleting(target);
              setMenuFor(null);
            }}
          />
        );
      })()}

      {renaming && (
        <RenameListingDialog
          initial={renaming.title ?? renaming.listing_title ?? renaming.product_name ?? renaming.product_id}
          onClose={() => setRenaming(null)}
          onSubmit={async (t) => {
            await onRename(renaming, t);
          }}
        />
      )}

      {deleting && (
        <DeleteListingDialog
          title={deleting.title ?? deleting.listing_title ?? deleting.product_name ?? deleting.product_id}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await onDelete(deleting);
          }}
        />
      )}
    </div>
  );
}

/** Right-click menu — anchored at cursor, click-outside closes (effect on parent). */
function ListingRowContextMenu({
  anchor,
  onClose,
  onRename,
  onDelete,
}: {
  anchor: { x: number; y: number };
  onClose: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  const menuWidth = 180;
  const menuHeight = 140;
  const left = Math.min(anchor.x, window.innerWidth - menuWidth - 8);
  const top = Math.min(anchor.y, window.innerHeight - menuHeight - 8);
  return (
    <div
      data-listing-row-menu
      style={{ position: "fixed", left, top, zIndex: 50 }}
      className="w-45 overflow-hidden rounded-lg border border-border-muted bg-card shadow-card"
    >
      <ul className="py-1 text-sm">
        <li>
          <button
            type="button"
            onClick={onRename}
            className="flex w-full items-center px-3 py-2 text-left transition-colors hover:bg-background-muted"
          >
            重命名
          </button>
        </li>
        <li>
          <button
            type="button"
            onClick={onDelete}
            className="flex w-full items-center px-3 py-2 text-left text-error transition-colors hover:bg-error/10"
          >
            删除草稿
          </button>
        </li>
      </ul>
      <div className="border-t border-border-muted px-3 py-1 text-right text-[10px] text-foreground-subtle">
        <button onClick={onClose} className="hover:text-foreground">
          关闭
        </button>
      </div>
    </div>
  );
}

function RenameListingDialog({
  initial,
  onClose,
  onSubmit,
}: {
  initial: string;
  onClose: () => void;
  onSubmit: (title: string) => Promise<void>;
}) {
  const [text, setText] = useState(initial);
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open onOpenChange={(o) => !o && !busy && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>重命名 Listing 草稿</DialogTitle>
          <DialogDescription>
            留空将恢复默认（候选商品名称）。
          </DialogDescription>
        </DialogHeader>
        <Input
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void (async () => {
                setBusy(true);
                try {
                  await onSubmit(text.trim());
                } finally {
                  setBusy(false);
                }
              })();
            }
          }}
        />
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            disabled={busy || !text.trim()}
            onClick={() => {
              void (async () => {
                setBusy(true);
                try {
                  await onSubmit(text.trim());
                } finally {
                  setBusy(false);
                }
              })();
            }}
          >
            {busy ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeleteListingDialog({
  title,
  onClose,
  onConfirm,
}: {
  title: string;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog open onOpenChange={(o) => !o && !busy && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-error">删除 Listing 草稿？</DialogTitle>
          <DialogDescription>
            将永久删除「{title}」。商品本身仍在选品结果中，可重新生成。
            <br />
            <span className="mt-2 inline-block font-medium text-error">
              此操作不可撤销。
            </span>
          </DialogDescription>
        </DialogHeader>
        {error && <p className="text-xs text-error">删除失败：{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            className="bg-error text-primary-foreground hover:bg-error/90"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onConfirm();
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
                setBusy(false);
              }
            }}
          >
            {busy ? "删除中…" : "确认删除"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ──── Read-only detail drawer ───────────────────────────────────────────────

function ListingDetailDrawer({
  runId,
  onOpenChange,
}: {
  runId: string | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [detail, setDetail] = useState<ListingRunDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!runId) {
      setDetail(null);
      return;
    }
    setLoading(true);
    void fetchListingRun(runId).then((d) => {
      setDetail(d);
      setLoading(false);
    });
  }, [runId]);

  const draft = detail?.listing?.listing ?? null;

  return (
    <Drawer open={runId !== null} onOpenChange={onOpenChange}>
      <DrawerContent className="max-w-lg">
        <DrawerHeader>
          <DrawerTitle>Listing 草稿详情</DrawerTitle>
        </DrawerHeader>
        <DrawerBody>
          {loading ? (
            <div className="space-y-3">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          ) : !detail ? (
            <p className="text-sm text-foreground-muted">
              加载失败——草稿可能已被清除，请刷新列表。
            </p>
          ) : !draft ? (
            <p className="text-sm text-foreground-muted">
              该记录的草稿数据缺失（历史遗留或损坏行）。
            </p>
          ) : (
            <div className="space-y-5">
              {detail.listing?.hard_failed && (
                <div
                  data-testid="hard-fail-banner"
                  className="space-y-1.5 rounded-lg border border-error/40 bg-error/5 px-3 py-2 text-xs"
                >
                  <p className="font-medium text-error">
                    存在硬校验失败字段——建议在对话中重新生成后再导出。
                  </p>
                  {(detail.listing.issues ?? [])
                    .filter((i) => i.severity === "hard_fail")
                    .map((i, idx) => (
                      <p key={idx} className="leading-relaxed text-error">
                        · <span className="font-mono">{i.field}</span>
                        {i.code ? ` (${i.code})` : ""} — {i.message}
                      </p>
                    ))}
                </div>
              )}
              {(() => {
                const auto = (detail.listing?.issues ?? []).filter(
                  (i) => i.severity === "auto_fixed",
                );
                if (auto.length === 0) return null;
                return (
                  <div
                    data-testid="auto-fixed-banner"
                    className="space-y-1.5 rounded-lg border border-info/30 bg-info/5 px-3 py-2 text-xs text-foreground-muted"
                  >
                    <p className="font-medium text-info">自动修复（已生效）</p>
                    {auto.map((i, idx) => (
                      <p key={idx} className="leading-relaxed">
                        · <span className="font-mono">{i.field}</span>
                        {i.code ? ` (${i.code})` : ""} — {i.message}
                      </p>
                    ))}
                  </div>
                );
              })()}
              <ReadonlySection
                label="商品标题"
                value={draft.item_name}
                count={`${draft.item_name.length}/${LISTING_FORMAT.titleMaxChars} 字符`}
              />
              {draft.bullet_point.length > 0 && (
                <ReadonlySection
                  label={`五点描述 · ${draft.bullet_point.length}/${LISTING_FORMAT.bulletCount} 条`}
                  value={draft.bullet_point
                    .map((b, i) => `${i + 1}. ${b}`)
                    .join("\n")}
                  count={`${draft.bullet_point.length} 条`}
                />
              )}
              <ReadonlySection
                label="商品描述"
                value={draft.product_description}
                count={`${draft.product_description.length}/${LISTING_FORMAT.descriptionMaxChars} 字符`}
              />
              <ReadonlySection
                label="搜索词"
                value={draft.generic_keyword}
                count={`${byteLength(draft.generic_keyword)}/${LISTING_FORMAT.searchTermsMaxBytes} 字节`}
              />
              {draft.product_type && (
                <ReadonlySection label="产品类型" value={draft.product_type} />
              )}
              {draft.subject_matter && (
                <ReadonlySection label="主题" value={draft.subject_matter} />
              )}
              {draft.target_audience && (
                <ReadonlySection label="目标受众" value={draft.target_audience} />
              )}
            </div>
          )}
        </DrawerBody>
        <DrawerFooter>
          <span className="mr-auto text-[11px] text-foreground-subtle">
            {detail ? `${detail.market_code} · ${formatTime(detail.created_at)}` : ""}
          </span>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </DrawerFooter>
      </DrawerContent>
    </Drawer>
  );
}

function ReadonlySection({
  label,
  value,
  count,
}: {
  label: string;
  value: string;
  count?: string;
}) {
  return (
    <section>
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-xs font-semibold text-foreground-subtle">
          {label}
        </span>
        <span className="flex items-center gap-2">
          {count && (
            <span className="text-[11px] tabular-nums text-foreground-subtle">
              {count}
            </span>
          )}
          <CopyButton text={value} />
        </span>
      </div>
      <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-border-muted bg-background-muted px-3 py-2.5 text-[13px] leading-relaxed text-foreground">
        {value}
      </pre>
    </section>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="rounded-md px-2 py-0.5 text-[11px] text-foreground-subtle transition-colors hover:bg-background-strong hover:text-foreground"
    >
      {copied ? "已复制" : "复制"}
    </button>
  );
}

function formatTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString("zh-CN", { hour12: false });
}
