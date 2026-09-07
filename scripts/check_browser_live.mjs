/**
 * 浏览器真实链路检查(live,需 LLM 键):发一条 chat,断言 agent 流式回复到达。
 * 用法:node scripts/check_browser_live.mjs [--url http://127.0.0.1:3000]
 */
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
};
const URL = arg("url", "http://127.0.0.1:3000");
const PROMPT = arg("prompt", "用一句话介绍你自己");

const EDGE = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find((p) => existsSync(p));

const browser = await chromium.launch({ executablePath: EDGE, headless: true });
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

let chatStatus = "no-request";
page.on("response", (r) => {
  if (r.url().includes("/api/chat/run")) chatStatus = "status=" + r.status();
});
await page.goto(URL, { waitUntil: "networkidle", timeout: 20000 });

const composer = page.locator("textarea").first();
await composer.waitFor({ state: "visible", timeout: 10000 });
await composer.fill(PROMPT);
await composer.press("Enter");

// 轮询:页面文本显著增长(用户气泡 + agent 回复)即通过,最长 120s
const bodyText = () =>
  page.locator("body").innerText().then((t) => t.replace(/\s+/g, " "));
const baseline = (await bodyText()).length;
let finalText = "";
let ok = false;
for (let i = 0; i < 24; i++) {
  await page.waitForTimeout(5000);
  finalText = await bodyText();
  if (finalText.length > baseline + 60) {
    ok = true;
    break;
  }
}

const tail = finalText.slice(-160);
console.log(`${ok ? "PASS" : "FAIL"}  收到 agent 流式回复(页面文本增长 ${finalText.length - baseline} 字符)`);
console.log(`页面尾部: …${tail}`);
console.log(`${errors.length === 0 ? "PASS" : "WARN"}  页面异常 ${errors.length} 个${errors.length ? ": " + errors.join(" | ").slice(0, 300) : ""}`);
await page.screenshot({ path: "docs/screenshots/vite-live-chat.png", fullPage: false });
console.log("截图: docs/screenshots/vite-live-chat.png");
await browser.close();
process.exit(ok ? 0 : 1);
