"use client";

import { useTheme } from "@appica/ui-react/hooks/use-theme";
import {
  IconLogo,
  IconMenu,
  IconMoon,
  IconSettings,
  IconSun,
} from "@/components/icons";
import type { ConfigSnapshot } from "@/lib/selection-api";

interface ChatHeaderProps {
  configSnapshot: ConfigSnapshot | null;
  busy: boolean;
  onNewSession: () => void;
  onOpenConfig: () => void;
  /** ui-refresh open the <md sidebar drawer. */
  onToggleSidebar?: () => void;
  /** ui-refresh artifact panel toggle (chat view, ≥xl). */
  artifactAvailable?: boolean;
  artifactOpen?: boolean;
  onToggleArtifact?: () => void;
}

function modelLabel(cfg: ConfigSnapshot | null): string {
  if (!cfg) return "加载中…";
  if (cfg.effective_selection) {
    return `${cfg.effective_selection.provider} · ${cfg.effective_selection.model}`;
  }
  if (cfg.api_key_set) return `${cfg.primary_provider} · ${cfg.primary_model}`;
  return "未配置模型";
}

export function ChatHeader({
  configSnapshot,
  busy,
  onNewSession,
  onOpenConfig,
  onToggleSidebar,
  artifactAvailable = false,
  artifactOpen = false,
  onToggleArtifact,
}: ChatHeaderProps) {
  const { resolvedTheme, setTheme, mounted } = useTheme();
  const isDark = resolvedTheme === "dark";

  return (
    <header className="flex items-center gap-3 border-b border-border-muted px-6 py-3">
      {onToggleSidebar && (
        <button
          type="button"
          id="sidebar-toggle"
          onClick={onToggleSidebar}
          title="打开会话侧栏"
          aria-label="打开会话侧栏"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-sm text-foreground-muted transition-colors hover:bg-background-muted hover:text-foreground md:hidden"
        >
          <IconMenu />
        </button>
      )}
      <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary-soft text-accent">
        <IconLogo width={18} height={18} />
      </div>
      <div className="min-w-0">
        <p className="text-[15px] font-semibold leading-tight text-foreground">
          选品智能体
        </p>
        <p
          className="truncate text-xs text-foreground-subtle"
          title="当前生效的选品模型（配置页保存即热重载）"
        >
          {modelLabel(configSnapshot)}
        </p>
      </div>

      <div className="ml-auto flex items-center gap-1.5">
        {artifactAvailable && onToggleArtifact && (
          <button
            type="button"
            onClick={onToggleArtifact}
            title={artifactOpen ? "收起 Listing 产物栏" : "查看 Listing 产物栏"}
            className={`hidden h-8 items-center gap-1.5 rounded-md px-3 text-xs font-medium transition-colors xl:flex ${
              artifactOpen
                ? "bg-primary-subtle text-accent"
                : "text-foreground-muted hover:bg-background-muted hover:text-foreground"
            }`}
          >
            <IconMenu width={12} height={12} />
            产物栏
          </button>
        )}
        <button
          type="button"
          onClick={onNewSession}
          disabled={busy}
          title="开始新会话"
          className="h-8 rounded-md px-3 text-xs font-medium text-foreground-muted transition-colors hover:bg-background-muted hover:text-foreground disabled:opacity-40"
        >
          新会话
        </button>
        <button
          type="button"
          onClick={onOpenConfig}
          title="模型与供应商配置"
          className="flex h-8 w-8 items-center justify-center rounded-full text-foreground-muted transition-colors hover:bg-background-muted hover:text-foreground"
        >
          <IconSettings />
        </button>
        {mounted && (
          <button
            type="button"
            onClick={() => setTheme(isDark ? "light" : "dark")}
            title={isDark ? "切换到浅色模式" : "切换到深色模式"}
            className="flex h-8 w-8 items-center justify-center rounded-full text-[15px] text-foreground-muted transition-colors hover:bg-background-muted hover:text-foreground"
          >
            {isDark ? <IconMoon /> : <IconSun />}
          </button>
        )}
      </div>
    </header>
  );
}
