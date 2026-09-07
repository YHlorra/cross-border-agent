// sidebar session right-click context menu .
// Validates the 5 menu items render + 置顶 toggles the 📌 badge. The
// underlying PATCH/archive/markUnread actions are wire-tested in
// tests/test_session_meta.py (10 SEAM cases including archived toggle and
// cascade delete). Playwright over multiple right-click → click cycles is
// brittle with HMR'd dev servers; this smoke keeps only the parts that
// reliably exercise the rendered DOM.
//
// Pre-run: clear all session_meta rows so the 置顶 assertion is deterministic.
// Usage: node scripts/p14_session_context_menu.mjs [--url http://127.0.0.1:3000]
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";
import { execSync } from "node:child_process";

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
};
const URL = arg("url", "http://127.0.0.1:3000");

const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => existsSync(p));

// Clear all session_meta rows so the 置顶 assertion is deterministic.
try {
  execSync(
    ".venv\\Scripts\\python.exe -c \"import sqlite3; c=sqlite3.connect('data/selection/memory.db'); c.execute('UPDATE session_meta SET pinned=0, archived=0'); c.commit()\"",
    { stdio: "ignore" },
  );
} catch {
  // best-effort; tests will still surface real failures
}

const browser = await chromium.launch({ headless: true, executablePath: EDGE });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => {
  if (m.type() === "error") console.log("CONSOLE-ERR:", m.text().slice(0, 200));
});

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
};

try {
  await page.goto(URL, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(2000);

  // Ensure at least one session exists in the sidebar (any of the smoke
  // leftovers from earlier runs; if empty the smoke can't run).
  const sessionRow = page.locator("aside button.text-left").first();
  const visible = await sessionRow.isVisible().catch(() => false);
  check("侧栏存在至少 1 个会话行", visible);
  if (!visible) {
    await page.screenshot({ path: "docs/screenshots/p14-no-session.png" });
    process.exit(1);
  }

  // Open context menu via right-click on the first row.
  await sessionRow.click({ button: "right" });
  await page.waitForTimeout(500);
  const menu = page.locator("[data-session-menu]");
  check("ContextMenu 出现", await menu.isVisible());
  await page.screenshot({ path: "docs/screenshots/p14-01-context-menu.png" });

  // 5 menu items render correctly
  for (const label of [
    "置顶任务",
    "重命名任务",
    "归档任务",
    "标记为未读",
    "在分屏打开",
  ]) {
    const visible = await menu
      .locator("button", { hasText: label })
      .first()
      .isVisible()
      .catch(() => false);
    check(`菜单项：${label}`, visible);
  }
  check(
    "「在分屏打开」禁用（即将上线）",
    await menu
      .locator("button", { hasText: "在分屏打开" })
      .first()
      .isDisabled()
      .catch(() => false),
  );

  // ① 置顶：点击后应出现 📌
  await menu.locator("button", { hasText: "置顶任务" }).first().click();
  await page.waitForSelector('aside [title="已置顶"]', { timeout: 10000 });
  check(
    "置顶后侧栏出现已置顶指示（📌）",
    (await page.locator('aside [title="已置顶"]').count()) > 0,
  );

  check("无页面运行时错误", errors.length === 0, errors.slice(0, 2).join(" | "));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);