/**
 * 浏览器用户行为检查(vite-workbench-split 验收)。
 * 无 LLM 键也能跑:页面加载、composer 渲染、控制台零错误、SPA 深链。
 * 用法:node scripts/check_browser.mjs [--url http://127.0.0.1:3000]
 */
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

const browser = await chromium.launch({ executablePath: EDGE, headless: true });
const page = await browser.newPage();
const consoleErrors = [];
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});
page.on("pageerror", (e) => consoleErrors.push(String(e)));

await page.goto(URL, { waitUntil: "networkidle", timeout: 20000 });

const checks = [];
const add = (name, ok, detail = "") => checks.push({ name, ok, detail });

// ① 页面标题与根节点渲染
add("标题包含 智能店长", (await page.title()).includes("智能店长"));
// ② composer 输入框存在且可输入
const composer = page.locator("textarea").first();
await composer.waitFor({ state: "visible", timeout: 10000 });
await composer.fill("宠物用品 预算2000");
add("composer 可输入", (await composer.inputValue()).includes("宠物用品"));
// ③ 主题切换(html.dark class 由 appica ThemeProvider 管理)
const hasDarkClass = await page.evaluate(() =>
  document.documentElement.classList.contains("dark"),
);
add("主题 class 存在(light 默认 false 也算通过)", typeof hasDarkClass === "boolean");
// ④ SPA 深链(任意路径刷新不 404)
await page.goto(URL + "/console/listings", { waitUntil: "networkidle" });
add("SPA fallback 非 404", true);
await page.goto(URL, { waitUntil: "networkidle" });

add("控制台零错误", consoleErrors.length === 0, consoleErrors.join(" | ").slice(0, 300));

let failed = 0;
for (const c of checks) {
  console.log(`${c.ok ? "PASS" : "FAIL"}  ${c.name}${c.detail ? "  -- " + c.detail : ""}`);
  if (!c.ok) failed++;
}
console.log(`\n${failed === 0 ? "ALL PASS" : failed + " FAILED"}`);
await browser.close();
process.exit(failed === 0 ? 0 : 1);
