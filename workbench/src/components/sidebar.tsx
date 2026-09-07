"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@appica/ui-react/button";
import { Badge } from "@appica/ui-react/badge";
import {
  IconPlus,
  IconPackage,
  IconHeart,
  IconList,
  IconSettings,
  IconStop,
  IconRefresh,
} from "@/components/icons";
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
  TooltipProvider,
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@appica/ui-react/tooltip";
import { ScrollArea } from "@appica/ui-react/scroll-area";

import type { SessionListItem } from "@/lib/selection-types";
import { relativeTime } from "@/lib/relative-time";
import type { ConfigSnapshot } from "@/lib/selection-api";
import { deleteSession, updateSessionMeta } from "@/lib/selection-api";
import {
  PinIcon,
  PinFilledIcon,
  PencilLineIcon,
  TrashIcon,
} from "@/components/icons";

interface SidebarProps {
  sessions: SessionListItem[];
  onRefreshSessions: () => Promise<void>;
  onClearAllSessions: () => Promise<void>;
  configSnapshot?: ConfigSnapshot | null;
  onOpenConfig: () => void;
  onOpenSession: (sessionId: string) => void;
  /**  — 新建任务（= 新建会话，语义更名，机制零改动）。 */
  onNewTask: () => void;
  /**  — Listing 列表（已持久化草稿的产物视图）。 */
  onOpenListings: () => void;
  /** 偏好长期记忆的 CRUD surface。 */
  onOpenPreferences: () => void;
  /**  addendum — ids of sessions currently running (live indicator). */
  runningSessionIds: ReadonlySet<string>;
  /**  addendum — abort a specific session's in-flight run. */
  onStopSession: (sessionId: string) => void;
  /** ui-refresh current workbench view, drives the nav active state. */
  activeView?: "chat" | "config" | "listings" | "preferences";
  /** ui-refresh focused session, drives the row selected state. */
  activeSessionId?: string | null;
  /** ui-refresh sessionId → last tool summary (one-line progress). */
  sessionSummaries?: Record<string, string>;
}

const EPOCH = "1970-01-01T00:00:00.000Z";

/** Sidebar shows a row unread when last_event_at is newer than last_read_at.
 *  Empty/null last_read_at counts as "never read" — fresh sessions that
 *  have at least one event appear unread until the user opens them. */
function isUnread(s: SessionListItem): boolean {
  if (!s.last_event_at) return false;
  if (!s.last_read_at) return s.event_count > 0;
  if (s.last_read_at === EPOCH) return true;
  return s.last_event_at > s.last_read_at;
}

export function Sidebar({
  sessions,
  runningSessionIds,
  onRefreshSessions,
  onClearAllSessions,
  configSnapshot,
  onOpenConfig,
  onOpenSession,
  onNewTask,
  onOpenListings,
  onOpenPreferences,
  onStopSession,
  activeView,
  activeSessionId,
  sessionSummaries,
}: SidebarProps) {
  // ui-refresh case-insensitive substring filter over session titles.
  const [filter, setFilter] = useState("");
  const [menu, setMenu] = useState<{
    sessionId: string;
    x: number;
    y: number;
  } | null>(null);
  const [renaming, setRenaming] = useState<{
    sessionId: string;
    currentTitle: string;
  } | null>(null);
  const [deleting, setDeleting] = useState<{
    sessionId: string;
    title: string;
  } | null>(null);

  // Click-outside / Escape closes the right-click menu.
  useEffect(() => {
    if (!menu) return;
    const onDocClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null;
      if (!target?.closest?.("[data-session-menu]")) setMenu(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(null);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  const needle = filter.trim().toLowerCase();
  const visibleSessions = sessions
    .filter(
      (s) =>
        needle === "" ||
        (s.title ?? s.session_id.slice(0, 8)).toLowerCase().includes(needle),
    );
  const menuSession = menu ? sessions.find((s) => s.session_id === menu.sessionId) : undefined;
  const modelLabel = configSnapshot?.effective_selection
    ? `${configSnapshot.effective_selection.provider}/${configSnapshot.effective_selection.model}`
    : configSnapshot?.api_key_set
      ? `${configSnapshot.primary_provider}/${configSnapshot.primary_model}`
      : "未配置";

  return (
    <aside className="flex h-full w-60 shrink-0 flex-col gap-2 bg-sidebar p-2">
<TooltipProvider delay={150}>
        <div className="flex items-center gap-2 rounded-lg px-4 py-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-accent">
          <span className="text-sm font-bold text-accent-contrast">CB</span>
        </div>
        <span className="text-sm font-bold tracking-tight">
          跨境电商智能店长
        </span>
      </div>

      <nav className="rounded-lg bg-card p-2">
        {/*  单对话 IA — 侧栏不再是"模块即地点"导航（旧的选品/
            Listing 生成/监控/店铺管理入口从属谎言，死按钮即物证）。
            改为：主操作（新建任务）+ 产物视图（商品管理 Soon、Listing
            列表）+ 配置；业务流由对话内斜杠命令触发。 */}
        <Button
          className="h-10 w-full justify-center gap-2 rounded-lg bg-accent text-sm font-semibold text-accent-contrast hover:bg-accent-hover"
          onClick={onNewTask}
          title="新建一个任务（清空当前对话，从新会话开始）"
        >
          <IconPlus size={18} />
          新建任务
        </Button>

        <div className="mt-2 space-y-1">
          {/* 商品管理 — 模块未到交付阶段，诚实灰置（Soon ≠ 死按钮：
              Soon 明示不可用，阶段落地后激活）。 */}
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  disabled
                  className="h-10 w-full justify-start gap-3 text-sm font-medium text-foreground-muted"
                >
                  <IconPackage size={18} />
                  <span>商品管理</span>
                  <Badge variant="outline" className="ml-auto text-[10px]">
                    Soon
                  </Badge>
                </Button>
              }
            />
            <TooltipContent>模块随交付阶段上线</TooltipContent>
          </Tooltip>

          <Button
            variant="ghost"
            className={`h-10 w-full justify-start gap-3 text-sm font-medium transition-colors ${
              activeView === "preferences"
                ? "bg-primary-subtle text-accent hover:bg-primary-subtle hover:text-accent"
                : "text-foreground-muted hover:bg-background-muted hover:text-foreground"
            }`}
            onClick={onOpenPreferences}
            title="长期偏好记忆（系统自动按相关性注入）"
          >
            <IconHeart size={18} />
            <span>偏好</span>
          </Button>

          <Button
            variant="ghost"
            className={`h-10 w-full justify-start gap-3 text-sm font-medium transition-colors ${
              activeView === "listings"
                ? "bg-primary-subtle text-accent hover:bg-primary-subtle hover:text-accent"
                : "text-foreground-muted hover:bg-background-muted hover:text-foreground"
            }`}
            onClick={onOpenListings}
            title="查看历次生成的 Listing 草稿"
          >
            <IconList size={18} />
            <span>Listing 列表</span>
          </Button>

          <Button
            variant="ghost"
            className={`h-10 w-full justify-start gap-3 text-sm font-medium transition-colors ${
              activeView === "config"
                ? "bg-primary-subtle text-accent hover:bg-primary-subtle hover:text-accent"
                : "text-foreground-muted hover:bg-background-muted hover:text-foreground"
            }`}
            onClick={onOpenConfig}
          >
            <IconSettings size={18} />
            <span>LLM 配置</span>
          </Button>
        </div>
      </nav>

      <div className="flex flex-1 flex-col overflow-hidden rounded-lg bg-card p-2">
        <div className="flex items-center justify-between px-2 py-2">
          <span className="text-xs font-semibold text-foreground-subtle">
            任务列表
          </span>
          <button
            onClick={() => void onRefreshSessions()}
            className="text-[10px] text-foreground-subtle transition-colors hover:text-foreground"
            title="刷新"
          >
            <IconRefresh size={14} />
          </button>
        </div>

        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setFilter("");
          }}
          placeholder="过滤任务…"
          className="mb-1 h-7 w-full rounded-md border border-border-muted bg-background px-2 text-xs text-foreground outline-none placeholder:text-foreground-subtle focus:border-accent/40"
        />

        <ScrollArea orientation="vertical" className="flex-1">
          {visibleSessions.length === 0 ? (
            <p className="px-2 py-4 text-xs text-foreground-subtle">
              暂无任务。新建任务并运行一次选品后会显示在这里。
            </p>
          ) : (
            <ul className="space-y-0.5">
              {visibleSessions.map((s) => {
            const isRunning = runningSessionIds.has(s.session_id);
            const unread = isUnread(s);
            const selected = activeSessionId === s.session_id;
            const titleText = s.title ?? s.session_id.slice(0, 8);
            return (
                <li key={s.session_id}>
                  <div
                    className={`group flex items-center gap-1 rounded-md px-2 py-2 transition-colors ${
                      selected
                        ? "bg-background-muted shadow-[inset_2px_0_0_0_var(--accent)]"
                        : isRunning
                          ? "bg-background-muted/40"
                          : ""
                    } hover:bg-background-muted`}
                  >
                    <button
                      className="min-w-0 flex-1 text-left"
                      onClick={() => onOpenSession(s.session_id)}
                      onContextMenu={(e) => {
                        e.preventDefault();
                        setMenu({
                          sessionId: s.session_id,
                          x: e.clientX,
                          y: e.clientY,
                        });
                      }}
                      title={`${titleText}${isRunning ? "（运行中）" : ""}`}
                    >
                      <span className="flex w-full items-center gap-2">
                        <span
                          className={`h-2 w-2 shrink-0 rounded-full ${
                            s.has_failed
                              ? "bg-error"
                              : isRunning
                                ? "bg-blue-500 animate-pulse"
                                : unread
                                  ? "bg-blue-400"
                                  : "bg-accent"
                          }`}
                        />
                        {s.pinned && (
                          <span
                            className="text-[10px] text-foreground-muted"
                            title="已置顶"
                          >
                            📌
                          </span>
                        )}
                        <span className="flex-1 truncate text-xs text-foreground-muted">
                          {titleText}…
                        </span>
                      </span>
                      <span className="flex w-full items-center gap-1 truncate pl-4 text-[11px] text-foreground-subtle">
                        <span className="shrink-0 tabular-nums">
                          {relativeTime(s.last_event_at)}
                        </span>
                        {sessionSummaries?.[s.session_id] && (
                          <>
                            <span className="shrink-0">·</span>
                            <span className="truncate">
                              {sessionSummaries[s.session_id]}
                            </span>
                          </>
                        )}
                      </span>
                    </button>
                    {isRunning && (
                      <button
                        type="button"
                        onClick={() => onStopSession(s.session_id)}
                        title="停止该会话的运行"
                        className="shrink-0 px-1.5 py-1 text-foreground-subtle transition-colors hover:text-error"
                      >
                        <IconStop size={14} />
                      </button>
                    )}
                    <SessionHoverActions
                      session={s}
                      onRename={() => {
                        setRenaming({
                          sessionId: s.session_id,
                          currentTitle: s.title ?? "",
                        });
                      }}
                      onDelete={() => {
                        setDeleting({
                          sessionId: s.session_id,
                          title: s.title ?? s.session_id.slice(0, 8),
                        });
                      }}
                      onPatch={async (patch) => {
                        await updateSessionMeta(s.session_id, patch);
                        await onRefreshSessions();
                      }}
                    />
                  </div>
                </li>
              );
            })}
            </ul>
          )}
        </ScrollArea>

        </div>

      <div className="rounded-lg bg-card p-3">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-background-muted text-xs font-semibold">
            U
          </div>
          <div className="flex-1 overflow-hidden">
            <p className="truncate text-sm font-medium">Demo User</p>
            <p
              className="truncate text-xs text-foreground-subtle"
              title={modelLabel}
            >
              {modelLabel}
            </p>
          </div>
        </div>
      </div>

      {menu && menuSession && (
        <SessionContextMenu
          anchor={{ x: menu.x, y: menu.y }}
          session={menuSession}
          onClose={() => setMenu(null)}
          onRename={() => {
            setRenaming({
              sessionId: menuSession.session_id,
              currentTitle: menuSession.title ?? "",
            });
            setMenu(null);
          }}
          onDelete={() => {
            setDeleting({
              sessionId: menuSession.session_id,
              title: menuSession.title ?? menuSession.session_id.slice(0, 8),
            });
            setMenu(null);
          }}
          onAction={async (patch) => {
            await updateSessionMeta(menuSession.session_id, patch);
            setMenu(null);
            await onRefreshSessions();
          }}
        />
      )}

      {renaming && (
        <RenameDialog
          currentTitle={renaming.currentTitle}
          onClose={() => setRenaming(null)}
          onSubmit={async (title) => {
            const t = title.trim();
            await updateSessionMeta(renaming.sessionId, {
              title: t === "" ? undefined : t,
            });
            setRenaming(null);
            await onRefreshSessions();
          }}
        />
      )}

      {deleting && (
        <DeleteSessionDialog
          title={deleting.title}
          sessionId={deleting.sessionId}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            const ok = await deleteSession(deleting.sessionId);
            setDeleting(null);
            if (ok) {
              await onRefreshSessions();
            }
          }}
        />
      )}
      </TooltipProvider>
    </aside>
  );
}

/** Right-click menu — anchored at cursor position, click-outside closes.
 *  All actions are best-effort server writes; on failure we surface the
 *  error inline via Alert (no toast layer yet). */
function SessionContextMenu({
  anchor,
  session,
  onClose,
  onRename,
  onDelete,
  onAction,
}: {
  anchor: { x: number; y: number };
  session: SessionListItem;
  onClose: () => void;
  onRename: () => void;
  onDelete: () => void;
  onAction: (patch: {
    pinned?: boolean;
    last_read_at?: string;
  }) => Promise<void>;
}) {
  const [error, setError] = useState<string | null>(null);
  const run = async (patch: Parameters<typeof onAction>[0]) => {
    try {
      await onAction(patch);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // Clamp menu inside to the viewport.
  const menuWidth = 200;
  const menuHeight = 280;
  const left = Math.min(anchor.x, window.innerWidth - menuWidth - 8);
  const top = Math.min(anchor.y, window.innerHeight - menuHeight - 8);

  return (
    <div
      data-session-menu
      style={{ position: "fixed", left, top, zIndex: 50 }}
      className="w-50 overflow-hidden rounded-lg border border-border-muted bg-card shadow-card"
    >
      <ul className="py-1 text-sm">
        <MenuItem
          label={session.pinned ? "取消置顶任务" : "置顶任务"}
          onSelect={() => {
            void run({ pinned: !session.pinned });
          }}
        />
        <MenuItem
          label="重命名任务"
          onSelect={() => {
            onRename();
            setError(null);
          }}
        />
        <MenuItem
          label="标记为未读"
          onSelect={() => {
            // Sentinel EPOCH — sidebar isUnread treats it as always unread.
            void run({ last_read_at: EPOCH });
          }}
        />
        <MenuItem
          label="在分屏打开"
          disabled
          title="即将上线"
          onSelect={() => {}}
        />
        <li className="my-1 border-t border-border-muted" aria-hidden />
        <MenuItem
          label="删除任务"
          destructive
          onSelect={() => {
            onDelete();
            setError(null);
          }}
        />
      </ul>
      {error && (
        <div className="border-t border-border-muted px-3 py-2 text-[11px] text-error">
          {error}
        </div>
      )}
      <div className="border-t border-border-muted px-3 py-1 text-right text-[10px] text-foreground-subtle">
        <button onClick={onClose} className="hover:text-foreground">
          关闭
        </button>
      </div>
    </div>
  );
}

/** Row-hover actions: small icon buttons revealed on row hover, mirroring the
 *  right-click menu's actions (pin / rename / archive / unread / split stub).
 *  Right-click stays available as a fallback; the buttons are the ergonomic
 *  primary surface for the same operations. Soft-only (archive ≠ delete — the
 *  destructive hard-delete lives on the preferences page per memory). */
function SessionHoverActions({
  session,
  onRename,
  onDelete,
  onPatch,
}: {
  session: SessionListItem;
  onRename: () => void;
  onDelete: () => void;
  onPatch: (patch: {
    pinned?: boolean;
    last_read_at?: string;
  }) => Promise<void>;
}) {
  const [error, setError] = useState<string | null>(null);
  const run = async (patch: Parameters<typeof onPatch>[0]) => {
    try {
      await onPatch(patch);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div
      data-session-hover-actions
      role="group"
      aria-label={`操作任务 ${session.title ?? session.session_id.slice(0, 8)}`}
      className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-within:opacity-100"
    >
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              type="button"
              aria-label={session.pinned ? "取消置顶" : "置顶"}
              onClick={() => void run({ pinned: !session.pinned })}
              className={`flex h-6 w-6 items-center justify-center rounded text-xs transition-colors hover:bg-background [&_svg]:stroke-[1.75] ${
                session.pinned
                  ? "text-foreground"
                  : "text-foreground-subtle hover:text-foreground"
              }`}
            >
              {session.pinned ? <PinFilledIcon /> : <PinIcon />}
            </button>
          }
        />
        <TooltipContent>{session.pinned ? "取消置顶任务" : "置顶任务"}</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              type="button"
              aria-label="重命名"
              onClick={onRename}
              className="flex h-6 w-6 items-center justify-center rounded text-xs text-foreground-subtle transition-colors hover:bg-background hover:text-foreground [&_svg]:stroke-[1.75]"
            >
              <PencilLineIcon />
            </button>
          }
        />
        <TooltipContent>重命名任务</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              type="button"
              aria-label="删除"
              onClick={onDelete}
              className="flex h-6 w-6 items-center justify-center rounded text-xs text-foreground-subtle transition-colors hover:bg-error/10 hover:text-error [&_svg]:stroke-[1.75]"
            >
              <TrashIcon />
            </button>
          }
        />
        <TooltipContent>删除任务（不可撤销）</TooltipContent>
      </Tooltip>
      {error && (
        <span
          role="alert"
          className="ml-1 max-w-32 truncate text-[10px] text-error"
          title={error}
        >
          {error}
        </span>
      )}
    </div>
  );
}

function MenuItem({
  label,
  onSelect,
  disabled,
  title,
  destructive = false,
}: {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  title?: string;
  destructive?: boolean;
}) {
  return (
    <li>
      <button
        type="button"
        disabled={disabled}
        title={title ?? label}
        onClick={onSelect}
        className={`flex w-full items-center px-3 py-2 text-left transition-colors disabled:cursor-not-allowed disabled:text-foreground-subtle ${
          destructive
            ? "text-error hover:bg-error/10"
            : "hover:bg-background-muted"
        }`}
      >
        {label}
      </button>
    </li>
  );
}

function RenameDialog({
  currentTitle,
  onClose,
  onSubmit,
}: {
  currentTitle: string;
  onClose: () => void;
  onSubmit: (title: string) => Promise<void>;
}) {
  const [text, setText] = useState(currentTitle);
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>重命名任务</DialogTitle>
          <DialogDescription>
            留空将恢复为默认显示（session_id 前 8 位）。
          </DialogDescription>
        </DialogHeader>
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="例如：宠物用品选品"
          autoFocus
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void (async () => {
                setBusy(true);
                try {
                  await onSubmit(text);
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
            disabled={busy}
            onClick={() => {
              void (async () => {
                setBusy(true);
                try {
                  await onSubmit(text);
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

/** 删除单个会话——单一红色按钮 + Modal 二次确认（与偏好页删除所有同模式）。 */
function DeleteSessionDialog({
  title,
  sessionId,
  onClose,
  onConfirm,
}: {
  title: string;
  sessionId: string;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog open onOpenChange={(o) => !o && !busy && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-error">删除任务？</DialogTitle>
          <DialogDescription>
            将删除「{title}」的聊天记录（消息与任务信息）。
            <br />
            选品报告与 Listing 草稿是产物，会保留在对应列表中。
            <br />
            <span className="mt-2 inline-block font-medium text-error">
              聊天记录删除后不可恢复。
            </span>
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p className="text-xs text-error">删除失败：{error}</p>
        )}
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
        <span className="sr-only">session_id: {sessionId}</span>
      </DialogContent>
    </Dialog>
  );
}

