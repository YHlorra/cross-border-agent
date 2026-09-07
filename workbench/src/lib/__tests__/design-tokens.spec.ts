/**
 * DESIGN.md ↔ index.css token 一致性快照(stripe-visual-rollout)。
 * DESIGN.md 变更时:重新 `designmd export --format css-vars DESIGN.md`
 * 比对后同步更新此期望表与 index.css。
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import path from "node:path";

const css = readFileSync(
  path.resolve(__dirname, "../../index.css"),
  "utf-8",
);

/** {css 变量名: DESIGN.md 导出的期望值} — 取自 designmd export --format css-vars */
const expected: Record<string, string> = {
  "--accent": "#6772e5",
  "--accent-hover": "#5469d4",
  "--primary": "#6772e5",
  "--foreground": "#32325d",
  "--foreground-muted": "#525f7f",
  "--canvas": "#f6f9fc",
  "--background": "#ffffff",
  "--success": "#1b7a4e",
  "--warning": "#9a6700",
  "--error": "#cd3d64",
};

function rawVar(name: string): string | null {
  const bare = name.replace(/^--/, "");
  const m = css.match(new RegExp(`--${bare}: ([^;]+);`));
  return m ? m[1].trim() : null;
}

describe("DESIGN.md ↔ index.css token 一致性", () => {
  it("核心 token 与规范一致(亮色)", () => {
    for (const [name, hex] of Object.entries(expected)) {
      const actual = rawVar(name);
      expect(actual, `${name} 应为 ${hex}`).toBe(hex);
    }
  });

  it("无 Apple 时代遗留色值", () => {
    for (const legacy of ["#0071e3", "#0a84ff", "#f5f5f7", "#1c1c1e"]) {
      expect(css.toLowerCase()).not.toContain(legacy);
    }
  });
});
