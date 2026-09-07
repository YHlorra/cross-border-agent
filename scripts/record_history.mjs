// Record the 0.0.2 history walkthrough as two PNGs for the README .
// Frame 1 (06-history.png): click a sidebar history entry → persisted report
// opens instantly (no LLM re-run).
// Frame 2 (07-history-followup.png): follow-up question carries the focused
// history as context (historyRunId) → new run completes.
//
// Usage: node scripts/record_history.mjs [--url http://127.0.0.1:3000] [--out docs/screenshots]
//
// Requires: dev server running on --url (with at least one persisted run in
// data/selection/memory.db); playwright-core; system Edge.
import { chromium } from "playwright-core";
import { mkdirSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
};

const URL = arg("url", "http://127.0.0.1:3000");
const OUT = resolve(arg("out", "docs/screenshots"));
const EDGE_PATHS = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  executablePath: EDGE_PATHS.find((p) => {
    try {
      return !!readdirSync(join(p, "..")).length && true;
    } catch {
      return false;
    }
  }),
});

const page = await browser.newPage({
  viewport: { width: 1280, height: 800 },
  deviceScaleFactor: 1.5,
});

await page.goto(URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("textarea", { timeout: 120_000 });
// Wait for sidebar history to hydrate (fetchHistory on mount).
await page.waitForSelector('aside button[title^="打开历史"]', { timeout: 30_000 });
await page.waitForTimeout(1_500);

// ── Frame 1: open a persisted run from the sidebar ─────────────────────────
await page.locator('aside button[title^="打开历史"]').first().click();
// runFromHistory renders phase:"final" — wait for the result card content in
// the main area (NOT "text=Listing", which matches the sidebar nav item).
// First hit also compiles the proxy route + spawns the agent bridge: 20s.
await page
  .locator("main")
  .getByText("综合评分")
  .first()
  .waitFor({ timeout: 20_000 });
await page.waitForTimeout(1_500);
await page.screenshot({ path: join(OUT, "06-history.png") });
console.log("saved 06-history.png");

// ── Frame 2: follow-up question anchored to the focused history ────────────
await page.fill("textarea", "再便宜的呢，1000 以内有没有能做的？");
await page.locator("textarea").click();
await page.waitForTimeout(300);
await page.keyboard.press("Enter");
// The follow-up is a real LLM run — wait for the new agent message to finish
// (composer re-enables / running indicator disappears), then settle.
await page.waitForTimeout(5_000);
const t0 = Date.now();
while ((Date.now() - t0) / 1000 < 120) {
  await page.waitForTimeout(2_000);
  const running = await page.getByText("思考中").count();
  const streaming = await page.getByText("正在撰写报告").count();
  if (running === 0 && streaming === 0) break;
}
await page.waitForTimeout(3_000);
await page.screenshot({ path: join(OUT, "07-history-followup.png") });
console.log("saved 07-history-followup.png");

await browser.close();
