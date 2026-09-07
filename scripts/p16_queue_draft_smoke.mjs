// p16 — ui-refresh P7 queue / cancel / draft-snapshot deterministic smoke.
// Needs a real LLM run for the queue lifecycle; gated like live tests.
// Usage: node scripts/p16_queue_draft_smoke.mjs [--url http://127.0.0.1:3000]
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
    if (m.text().startsWith("[queue-debug]")) console.log("DBG", m.text());
  });
  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3000);

  const ta = page.locator("textarea").nth(1); // [0] is Edge-internal in some builds; guard below
  const composer = (await ta.count()) > 0 ? ta : page.locator("textarea").first();
  await composer.click();

  // ① Start a real selection run (tools ⇒ stays live long enough to queue).
  //    Plain text — no slash, so the palette never intercepts the Enter.
  // general-agent-chat: plain text now goes to the fast general chat loop —
  // the queue/cancel timing needs a long-running run; since 
  //  slash rides the chat loop, whose LLM turns keep it
  // live just as long.
  await composer.fill("/选品 宠物饮水机 预算 200");
  await composer.press("Enter");
  await page.waitForTimeout(2500);
  const liveStrip = await page.locator('[role="status"]').count();
  check("运行中状态条出现", liveStrip > 0);

  // ② Queue a message while live → chip renders, composer clears.
  await composer.fill("/选品 无线耳机 预算 100");
  await composer.press("Enter");
  await page.waitForTimeout(600);
  const chipVisible = await page.getByText("排队 #1", { exact: false }).first().isVisible().catch(() => false);
  check("排队 chip 渲染", chipVisible);
  const composerCleared = (await composer.inputValue()) === "";
  check("入队后 composer 清空", composerCleared);

  // ③ On live running→final, the queue auto-sends: chip disappears, message
  //    becomes a new user bubble and a second run starts (strip reappears).
  await page
    .locator('[role="status"]')
    .first()
    .waitFor({ state: "detached", timeout: 360000 })
    .catch(() => {});
  await page.waitForTimeout(2500);
  const chipGone = (await page.getByText("排队 #1", { exact: false }).count()) === 0;
  check("终态后 chip 出队消失", chipGone);
  const autoSent = await page
    .locator("div.max-w-\\[78\\%\\]", { hasText: "无线耳机 预算 100" })
    .first()
    .isVisible()
    .catch(() => false);
  check("队列消息自动发送为用户气泡", autoSent);

  // ④ Cancel the auto-started follow-up run (stop button on the strip).
  const stripNow = await page.locator('[role="status"]').count();
  if (stripNow > 0) {
    await page.getByRole("button", { name: "停止", exact: true }).first().click();
    await page.waitForTimeout(1500);
    const cancelled = await page
      .getByText("已停止本次运行", { exact: false })
      .first()
      .isVisible()
      .catch(() => false);
    check("停止后取消态渲染", cancelled);
  } else {
    const mainTxt = await page.locator("main").innerText().catch(() => "(no main)");
    console.log("[DBG4-FAIL] main tail:", mainTxt.slice(-400));
    check("停止后取消态渲染", false, "第二条 run 未处于运行态");
  }

  // ⑤ Draft snapshot: type in this session, 新建任务, composer clears;
  //    switch back to the session row, draft restored.
  await composer.click();
  await composer.fill("草稿快照测试文本");
  // capture THIS session's row title while it is still selected (inset bar)
  const selectedRow = page.locator('aside ul li div[class*="inset"]').first();
  const rowText = (await selectedRow.textContent().catch(() => null)) ?? "";
  await page.getByRole("button", { name: "新建任务" }).first().click();
  await page.waitForTimeout(600);
  const cleared = (await composer.inputValue()) === "";
  check("新建任务后 composer 清空", cleared);
  const backRow = page
    .locator("aside ul li button")
    .filter({ hasText: rowText.slice(0, 6) })
    .first();
  await backRow.click();
  await page.waitForTimeout(800);
  const restored = (await composer.inputValue()) === "草稿快照测试文本";
  check("切回会话草稿恢复", restored);
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
