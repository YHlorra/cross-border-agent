// p15 — ui-refresh-20260905 structure smoke (P7).
// Asserts what's assertable without a live LLM run: token cleanup surfaces,
// icon SVGs, nav active state, filter box, status-strip absence when idle,
// <md drawer open/close (Scrim + Escape), and dual-theme screenshots.
// RunTimeline live behavior is covered by the computer-use full-link pass.
//
// Usage: node scripts/p15_ui_refresh_smoke.mjs [--url http://127.0.0.1:3000]
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
};
const URL = arg("url", "http://127.0.0.1:3000");

const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => existsSync(p));

const browser = await chromium.launch({ headless: true, executablePath: EDGE });
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
};
const errors = [];

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text().slice(0, 200));
  });

  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(4000);

  // ── P1 hero copy updated; favicon present
  check("P1 hero 文案指向偏好页", await page
    .getByText("长期偏好在「偏好」页维护", { exact: false })
    .first()
    .isVisible()
    .catch(() => false));
  const iconLink = await page.evaluate(() =>
    !!document.querySelector('link[rel*="icon"]'),
  );
  check("P1 favicon 存在", iconLink);

  // ── P3 header icons are SVGs now (no ☾/☀/⚙ glyphs)
  const glyphScan = await page.evaluate(() =>
    document.querySelector("header")?.innerText ?? "",
  );
  check("P3 header 无字符图标", !/[☾☀⚙◈]/.test(glyphScan), glyphScan.slice(0, 40));
  const headerSvgCount = await page.evaluate(
    () => document.querySelectorAll("header svg").length,
  );
  check("P3 header 含 SVG 图标", headerSvgCount >= 2, `svg=${headerSvgCount}`);

  // ── P2 status strip absent when idle
  const stripIdle = await page.locator('[role="status"]').count();
  check("P2 空闲无运行状态条", stripIdle === 0);

  // ── P5 filter input exists and is usable
  const filterBox = page.getByPlaceholder("过滤任务…");
  check("P5 过滤框存在", await filterBox.isVisible().catch(() => false));
  await filterBox.fill("不存在品类xyz").catch(() => {});
  await page.waitForTimeout(300);
  check("P5 过滤无匹配时列表为空", await page
    .getByText("暂无任务", { exact: false })
    .first()
    .isVisible()
    .catch(() => false));
  await filterBox.fill("");
  await page.waitForTimeout(200);

  // ── P2 nav active state (偏好)
  const prefBtn = page.locator("aside").getByText("偏好", { exact: true }).first();
  await prefBtn.click();
  await page.waitForTimeout(600);
  const prefActive = await page.evaluate(() => {
    const btns = [...document.querySelectorAll("aside button")];
    const b = btns.find((x) => x.textContent?.includes("偏好"));
    return b ? b.className.includes("bg-primary-subtle") : false;
  });
  check("P2 偏好入口 active 态", prefActive);
  await page.screenshot({ path: "docs/screenshots/ui-refresh-preferences.png" });

  // back to chat + light screenshot
  const newTask = page.getByRole("button", { name: "新建任务" }).first();
  await newTask.click();
  await page.waitForTimeout(500);
  check("P2 回到聊天后无 active 入口", await page.evaluate(() => {
    const btns = [...document.querySelectorAll("aside button")];
    return !btns.some(
      (x) =>
        x.className.includes("bg-primary-subtle") &&
        /偏好|Listing|LLM/.test(x.textContent ?? ""),
    );
  }));

  // ── dual theme screenshots ( ThemeScript theme mechanism works)
  await page.screenshot({ path: "docs/screenshots/ui-refresh-light.png" });
  const themeBtn = page.locator("header button[title*='模式']").first();
  await themeBtn.click();
  await page.waitForTimeout(500);
  const isDark = await page.evaluate(() =>
    document.documentElement.classList.contains("dark"),
  );
  check("P1 深浅色切换生效", isDark);
  await page.screenshot({ path: "docs/screenshots/ui-refresh-dark.png" });
  await themeBtn.click();
  await page.waitForTimeout(300);

  // ── P7 drawer at narrow viewport
  const narrow = await browser.newPage({ viewport: { width: 390, height: 844 } });
  narrow.on("pageerror", (e) => errors.push(String(e)));
  await narrow.goto(URL, { waitUntil: "domcontentloaded", timeout: 60000 });
  await narrow.waitForTimeout(4000);
  const asideHidden = await narrow.evaluate(() => {
    const a = document.querySelector("aside");
    return a ? getComputedStyle(a.closest("div") ?? a).display === "none" || a.clientWidth === 0 : false;
  });
  check("P7 窄屏侧栏默认收起", asideHidden);
  const burger = narrow.locator("#sidebar-toggle");
  check("P7 汉堡钮可见", await burger.isVisible().catch(() => false));
  await burger.click();
  await narrow.waitForTimeout(400);
  const drawerVisible = await narrow
    .locator('[role="dialog"][aria-label="会话侧栏"]')
    .isVisible()
    .catch(() => false);
  check("P7 抽屉打开", drawerVisible);
  await narrow.screenshot({ path: "docs/screenshots/ui-refresh-drawer.png" });
  await narrow.keyboard.press("Escape");
  await narrow.waitForTimeout(300);
  const drawerClosed = await narrow
    .locator('[role="dialog"][aria-label="会话侧栏"]')
    .count();
  check("P7 Esc 关闭抽屉", drawerClosed === 0);

  check("无页面运行时错误", errors.length === 0, errors.slice(0, 2).join(" | "));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
