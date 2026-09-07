"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { Card } from "@appica/ui-react/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@appica/ui-react/dialog";
import { Input } from "@appica/ui-react/input";
import { Skeleton } from "@appica/ui-react/skeleton";

import {
  addPreference,
  deletePreference,
  fetchPreferences,
  updatePreference,
  type Preference,
} from "@/lib/preferences-api";

/** Sidebar "偏好" entry — 偏好 RAG user CRUD surface.
 *  Composers no longer carry transient preference chips; preferences are
 *  long-term memory, set once here (run-time injection pending — ). */
export function PreferencesView({ onClearAllSessions }: { onClearAllSessions: () => Promise<void> }) {
  const [prefs, setPrefs] = useState<Preference[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Preference | null>(null);

  const refresh = useCallback(async () => {
    setPrefs(null);
    setPrefs(await fetchPreferences());
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onAdd = async () => {
    const t = text.trim();
    if (!t) return;
    setBusy(true);
    setError(null);
    const id = await addPreference(t);
    setBusy(false);
    if (id === null) {
      setError("添加失败——agent 离线或后端异常，请检查 dev server");
      return;
    }
    setText("");
    void refresh();
  };

  const onRemove = async (id: number) => {
    setBusy(true);
    const ok = await deletePreference(id);
    setBusy(false);
    if (!ok) setError("删除失败");
    void refresh();
  };

  const onEditSubmit = async (id: number, patch: { text?: string; category?: string }): Promise<void> => {
    setBusy(true);
    setError(null);
    const ok = await updatePreference(id, patch);
    setBusy(false);
    setEditing(null);
    if (!ok) setError("编辑失败——服务端可能未实现 PATCH /preferences/{id}");
    void refresh();
  };

  return (
    <div className="mx-auto max-w-4xl px-6 py-8 xl:max-w-5xl">
      <div className="mb-6">
        <h1 className="text-xl font-bold tracking-tight text-foreground-intense">
          偏好
        </h1>
        <p className="mt-1 text-sm text-foreground-muted">
          长期记忆；系统会在每次查询时按相关性自动注入到意图识别与评分参考。
        </p>
      </div>

      {/* Add form */}
      <Card frame={false} className="mb-6 w-full bg-card p-5">
        <label
          htmlFor="pref-text"
          className="mb-1.5 block text-xs font-semibold text-foreground-subtle"
        >
          新增偏好
        </label>
        <textarea
          id="pref-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          placeholder="例如：偏好高客单价 + 轻小件 + 高复购的商品"
          className="w-full resize-y rounded-lg border border-border bg-background-muted px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          disabled={busy}
        />
        {error && (
          <p className="mt-2 text-xs text-error">{error}</p>
        )}
        <div className="mt-3 flex items-center justify-end gap-2">
          <span className="mr-auto text-[11px] text-foreground-subtle">
            类目默认 <code className="rounded bg-background-muted px-1.5 py-0.5">user-added</code>
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refresh()}
            disabled={busy}
          >
            刷新
          </Button>
          <Button
            size="sm"
            onClick={() => void onAdd()}
            disabled={busy || !text.trim()}
          >
            保存偏好
          </Button>
        </div>
      </Card>

      {/* Active list */}
      {prefs === null ? (
        <div className="space-y-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : prefs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border-secondary p-10 text-center">
          <p className="text-sm text-foreground-muted">还没有偏好记忆</p>
          <p className="mt-1 text-xs text-foreground-subtle">
            在上方写一句，系统会按相关性自动使用
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {prefs.map((p) => (
            <li key={p.id}>
              <Card
                frame={false}
                className="border border-border-muted bg-card p-4 [--card-radius:var(--radius-md)]"
              >
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="mb-1 flex items-center gap-2">
                      <Badge variant="outline" className="text-[10px]">
                        {p.category}
                      </Badge>
                      <span className="text-[11px] text-foreground-subtle">
                        {formatTime(p.created_at)}
                      </span>
                      {p.reinforcement_count > 1 && (
                        <span className="text-[11px] text-foreground-subtle">
                          · 已强化 {p.reinforcement_count} 次
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-foreground">
                      {p.preference_text}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setEditing(p)}
                    disabled={busy}
                    className="shrink-0 text-foreground-subtle hover:text-foreground"
                    title="编辑偏好（重新嵌入）"
                  >
                    编辑
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void onRemove(p.id)}
                    disabled={busy}
                    className="shrink-0 text-foreground-subtle hover:text-error"
                    title="从记忆中移除"
                  >
                    移除
                  </Button>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      {/* 清除全部记忆：单一红色按钮 + Modal 二次确认 modal（用户打开即可知是危险操作） */}
      <div className="mt-8">
        <ClearAllMemoriesDialog onClearAll={onClearAllSessions} />
      </div>

      {editing && (
        <EditPreferenceDialog
          preference={editing}
          busy={busy}
          onClose={() => setEditing(null)}
          onSubmit={async (patch) => {
            await onEditSubmit(editing.id, patch);
          }}
        />
      )}
    </div>
  );
}

function formatTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString("zh-CN", { hour12: false });
}

/** 编辑偏好——修改文本会触发服务端重新嵌入；类别可独立改。 */
function EditPreferenceDialog({
  preference,
  busy,
  onClose,
  onSubmit,
}: {
  preference: Preference;
  busy: boolean;
  onClose: () => void;
  onSubmit: (patch: { text?: string; category?: string }) => Promise<void>;
}) {
  const [text, setText] = useState(preference.preference_text);
  const [category, setCategory] = useState(preference.category);
  return (
    <Dialog open onOpenChange={(o) => !o && !busy && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>编辑偏好</DialogTitle>
          <DialogDescription>
            修改文本将触发服务端重新嵌入（pgvector / sqlite-vec）；
            类别独立保存，不触发重嵌入。
          </DialogDescription>
        </DialogHeader>
        <label className="mb-1.5 block text-xs font-semibold text-foreground-subtle">
          偏好文本
        </label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          className="w-full resize-y rounded-lg border border-border bg-background-muted px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          disabled={busy}
          autoFocus
        />
        <label className="mt-3 mb-1.5 block text-xs font-semibold text-foreground-subtle">
          类别
        </label>
        <Input
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          disabled={busy}
          placeholder="例如：user-added"
        />
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            disabled={busy || !text.trim() || text.trim() === preference.preference_text && category === preference.category}
            onClick={async () => {
              const patch: { text?: string; category?: string } = {};
              if (text.trim() !== preference.preference_text) patch.text = text.trim();
              if (category !== preference.category) patch.category = category;
              await onSubmit(patch);
            }}
          >
            {busy ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 删除所有会话——单一红色按钮 + Modal 二次确认（modal 打开即明危险）。 */
function ClearAllMemoriesDialog({ onClearAll }: { onClearAll: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Button
        variant="outline"
        className="border-error/40 bg-error/10 text-error hover:bg-error/20 hover:text-error"
        onClick={() => setOpen(true)}
      >
        删除所有记忆
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-error">删除所有记忆？</DialogTitle>
            <DialogDescription>
              这会删除所有会话（含进行中）、侧栏历史、长期偏好。
              <br />
              <span className="mt-2 inline-block font-medium text-error">
                此操作不可撤销。
              </span>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={busy}
            >
              取消
            </Button>
            <Button
              className="bg-error text-primary-foreground hover:bg-error/90"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await onClearAll();
                  setOpen(false);
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "删除中…" : "确认删除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}