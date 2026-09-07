"use client";

import { useLayoutEffect, useRef, useState } from "react";

import {
  filterCommands,
  splitCommandQuery,
  type CommandDef,
} from "@/lib/commands";
import { IconArrowUp } from "@/components/icons";

/** Composer — sole job: text + send. Slash commands and preferences
 *  live elsewhere (commands palette / sidebar "偏好" entry). User can set
 *  long-term memory once in the preferences view and forget about toggling
 *  per query. */
interface ComposerProps {
  query: string;
  onQueryChange: (v: string) => void;
  running: boolean;
  onSubmit: () => void;
  onCancel: () => void;
}

export function Composer({
  query,
  onQueryChange,
  running,
  onSubmit,
  onCancel,
}: ComposerProps) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const canSubmit = query.trim().length > 0 && !running;

  // Slash-command palette — visible purely from the query (starts with "/"),
  // so IME composition never fights the trigger. Escape dismisses until the
  // next edit; Arrow/Tab/Enter pick while open (submit is suppressed then).
  // Tab = select highlighted; Shift+Tab = navigate prev (keyboard parity
  // with common picker UIs so power users don't need to reach for arrows).
  const [paletteIndex, setPaletteIndex] = useState(0);
  const [paletteDismissed, setPaletteDismissed] = useState(false);
  const paletteItems = query.startsWith("/")
    ? filterCommands(query)
    : ([] as CommandDef[]);
  const paletteOpen = paletteItems.length > 0 && !paletteDismissed;
  const activeIndex = Math.min(paletteIndex, paletteItems.length - 1);

  const pickCommand = (cmd: CommandDef) => {
    const { rest } = splitCommandQuery(query);
    onQueryChange(`/${cmd.label}${rest ? ` ${rest}` : " "}`);
    setPaletteDismissed(false);
    taRef.current?.focus();
  };

  // Auto-grow the textarea to fit its content (1–6 rows).
  useLayoutEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "0px";
    ta.style.height = `${Math.min(ta.scrollHeight, 144)}px`;
  }, [query]);

  return (
    <>
      <div className="relative rounded-xl border border-border bg-card shadow-card transition-colors focus-within:border-accent/50">
      {paletteOpen && (
        <div
          role="listbox"
          aria-label="斜杠命令"
          className="absolute bottom-full left-2 right-2 z-20 mb-2 overflow-hidden rounded-lg border border-border-muted bg-card shadow-card"
        >
          {paletteItems.map((cmd, i) => (
            <button
              key={cmd.id}
              type="button"
              role="option"
              aria-selected={i === activeIndex}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pickCommand(cmd)}
              className={`flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors ${
                i === activeIndex ? "bg-background-muted" : "hover:bg-background-muted/60"
              }`}
            >
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-primary-soft text-xs font-bold text-accent">
                /
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-foreground">
                  {cmd.usage}
                </span>
                <span className="block truncate text-[11px] text-foreground-subtle">
                  {cmd.description}
                </span>
              </span>
              {i === activeIndex && (
                <span className="shrink-0 text-[10px] text-foreground-subtle">
                  Enter / Tab 选中
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {running && <span className="marquee-line" aria-hidden />}
      <textarea
        ref={taRef}
        value={query}
        onChange={(e) => {
          setPaletteDismissed(false);
          setPaletteIndex(0);
          onQueryChange(e.target.value);
        }}
        onKeyDown={(e) => {
          // IME 安全：组合键输入期间不拦截任何键（Enter 属于候选确认）。
          if (e.nativeEvent.isComposing) return;
          if (paletteOpen) {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setPaletteIndex((activeIndex + 1) % paletteItems.length);
              return;
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setPaletteIndex(
                (activeIndex - 1 + paletteItems.length) % paletteItems.length,
              );
              return;
            }
            if (e.key === "Tab") {
              // Tab = pick highlighted (or next if Shift held); default Tab
              // would otherwise escape the textarea to the next focusable
              // element (chip / / / send) which is what users complained about.
              e.preventDefault();
              if (e.shiftKey) {
                setPaletteIndex(
                  (activeIndex - 1 + paletteItems.length) % paletteItems.length,
                );
              } else {
                pickCommand(paletteItems[activeIndex]);
              }
              return;
            }
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              pickCommand(paletteItems[activeIndex]);
              return;
            }
            if (e.key === "Escape") {
              e.preventDefault();
              setPaletteDismissed(true);
              return;
            }
          }
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            // While a run is live the workbench routes submit → queue
            // so the keyboard path stays open when running.
            if (canSubmit || running) onSubmit();
          }
        }}
        rows={1}
        placeholder="描述你想查的品类，或输入 / 唤起命令（/选品、/listing）"
        className="max-h-36 w-full resize-none bg-transparent px-4 pt-3.5 text-[15px] leading-relaxed text-foreground outline-none placeholder:text-foreground-subtle"
      />

      <div className="flex flex-wrap items-center gap-2 px-3 pb-3 pt-1">
        <div className="ml-auto flex items-center gap-2">
          <span className="hidden text-[11px] text-foreground-subtle sm:inline">
            Enter 发送 · Shift+Enter 换行 · / 唤起命令
          </span>
          {running ? (
            <button
              type="button"
              onClick={onCancel}
              title="停止本次运行"
              className="flex h-9 w-9 items-center justify-center rounded-full bg-background-strong text-foreground transition-transform hover:scale-105"
            >
              <span className="block h-2.5 w-2.5 rounded-[2px] bg-current" />
            </button>
          ) : (
            <button
              type="button"
              onClick={onSubmit}
              disabled={!canSubmit}
              title="发送"
              className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-[17px] font-medium text-primary-foreground transition-all hover:bg-accent-hover disabled:opacity-30 disabled:hover:bg-accent"
            >
              <IconArrowUp />
            </button>
          )}
        </div>
      </div>
      </div>
    </>
  );
}