// Record the 0.0.1 selection demo as PNG frames for the README GIF .
// Drives the real chat UI end to end: type query → budget → submit → capture
// node progress, streaming report, and the result reveal.
//
// Usage: node scripts/record_demo.mjs [--url http://127.0.0.1:3000] [--out <dir>]
//
// Requires: dev server running on --url; playwright-core (devDependency);
// Edge (no browser download — uses the system channel).
import { chromium } from "playwright-core";
import { mkdirSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
};

const URL = arg("url", "http://127.0.0.1:3000");
const OUT = resolve(arg("out", join(tmpdir(), "demo-frames")));
const CAPTURE_MS = 500;
const MAX_SECONDS = 90;
const EDGE_PATHS = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

mkdirSync(OUT, { recursive: true });
const existing = readdirSync(OUT).filter((f) => f.startsWith("frame-")).length;
if (existing > 0) throw new Error(`${OUT} already has ${existing} frames — clear it first`);

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

// Page load (first hit also compiles the route — keep it out of the recording).
await page.goto(URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector("textarea", { timeout: 120_000 });
await page.waitForTimeout(2_500);

let frame = 0;
const shoot = async () => {
  frame += 1;
  await page.screenshot({
    path: join(OUT, `frame-${String(frame).padStart(4, "0")}.png`),
  });
};

await shoot(); // hero / empty state

await page.fill('textarea', "宠物用品有什么好卖的？预算 2000 以内");
await page.fill('input[placeholder="预算 ¥"]', "2000");
// Enter-submit lives on the textarea's onKeyDown — refocus it after the budget fill.
await page.locator("textarea").click();
await page.waitForTimeout(300);
await shoot(); // filled composer
await page.keyboard.press("Enter");
await page.waitForSelector("text=正在", { timeout: 15_000 }).catch(() => {});

// Capture the whole run; stop shortly after the report's first heading renders.
const t0 = Date.now();
let reportSeen = false;
while ((Date.now() - t0) / 1000 < MAX_SECONDS) {
  await page.waitForTimeout(CAPTURE_MS);
  await shoot();
  if (!reportSeen && (await page.getByText("市场概览").count()) > 0) {
    reportSeen = true;
    // keep rolling ~12s so the tail of the report + result UI lands in the GIF
    for (let i = 0; i < 24; i++) {
      await page.waitForTimeout(CAPTURE_MS);
      await shoot();
    }
    break;
  }
}

await browser.close();
console.log(`recorded ${frame} frames -> ${OUT} (reportSeen=${reportSeen})`);
