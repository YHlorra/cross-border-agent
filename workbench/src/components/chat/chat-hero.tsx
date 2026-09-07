"use client";

import { Card } from "@appica/ui-react/card";

import { quickSuggestions } from "@/lib/mock-data";
import { IconLogo } from "@/components/icons";
import { COMMANDS } from "@/lib/commands";

interface ChatHeroProps {
  onPick: (text: string) => void;
}

export function ChatHero({ onPick }: ChatHeroProps) {
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "上午好" : hour < 18 ? "下午好" : "晚上好";

  // Slash-command chips — the discoverability backstop for the palette:
  // picking one goes through the same submitRouted path as typing "/…"。
  const commandChips = [
    { text: "/选品 智能宠物用品，预算 500" },
    { text: "/listing" },
  ];

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col items-center justify-center px-6 text-center">
      <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary-soft text-accent stagger-in">
        <IconLogo width={34} height={34} />
      </div>
      <h1
        className="mb-3 text-3xl font-bold tracking-tight text-foreground-intense stagger-in"
        style={{ animationDelay: "80ms" }}
      >
        {greeting}，我是跨境电商智能店长
      </h1>
      <p
        className="mb-8 max-w-md text-sm leading-relaxed text-foreground-muted stagger-in"
        style={{ animationDelay: "160ms" }}
      >
        说一句你想查的品类，或用斜杠命令唤起技能：选品报告、Listing
        草稿都在这个对话里完成。
      </p>

      <div
        className="mb-3 flex flex-wrap items-center justify-center gap-2 stagger-in"
        style={{ animationDelay: "200ms" }}
      >
        {commandChips.map((chip) => (
          <button
            key={chip.text}
            type="button"
            onClick={() => onPick(chip.text)}
            title={`命令 · ${COMMANDS.find((c) => chip.text.startsWith(`/${c.label}`))?.description ?? ""}`}
            className="flex items-center gap-2 rounded-md border border-border-muted bg-card px-3.5 py-1.5 text-xs font-medium text-foreground-muted shadow-card transition-colors hover:border-accent/40 hover:text-foreground"
          >
            {chip.text}
          </button>
        ))}
      </div>

      <div
        className="grid w-full grid-cols-2 gap-3 stagger-in"
        style={{ animationDelay: "240ms" }}
      >
        {quickSuggestions.map((s) => (
          <Card
            key={s.text}
            frame={false}
            className="card-hover cursor-pointer border border-border-muted bg-card p-4 text-left shadow-card [--card-radius:var(--radius-md)]"
            onClick={() => onPick(s.text)}
          >
            <div className="flex items-center gap-3">
              <span className="text-2xl">{s.icon}</span>
              <span className="text-sm font-medium text-foreground">
                {s.text}
              </span>
            </div>
          </Card>
        ))}
      </div>

      <p
        className="mt-8 text-xs text-foreground-subtle stagger-in"
        style={{ animationDelay: "320ms" }}
      >
        品类不设限 · 输入 / 查看全部命令 · 长期偏好在「偏好」页维护
      </p>
    </div>
  );
}
