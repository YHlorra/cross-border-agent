//  一次性 UI 冒烟 — 新侧栏 IA + 斜杠命令面板 + listings 视图 + 配置页汇总条。
// 镜像 p11 的 playwright-core + 系统 Edge 模式；无 LLM 依赖（只测静态渲染与交互接线）。
// Usage: node scripts/p12_single_chat_ia_smoke.mjs [--url http://127.0.0.1:3000]
import { chromium } from "playwright-core";
import { existsSync } from "node:fs";

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
};
const URL = arg("url", "http://127.0.0.1:3000");

const EDGE_PATHS = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
};

const browser = await chromium.launch({
  headless: true,
  executablePath: EDGE_PATHS.find((p) => existsSync(p)),
});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

try {
  await page.goto(URL, { waitUntil: "networkidle", timeout: 60000 });

  // ① 新侧栏：新建任务 / 商品管理(Soon) / Listing 列表 / 任务列表
  //（图标化后可访问名=「新建任务」；general-agent-chat 2026-09-06 图标库替换）
  const newTaskBtn = page.getByRole("button", { name: "新建任务" });
  check("sidebar 新建任务按钮", await newTaskBtn.first().isVisible());
  check("sidebar 商品管理 Soon", await page.getByText("商品管理", { exact: true }).first().isVisible());
  check("sidebar Listing 列表入口", await page.getByText("Listing 列表", { exact: true }).first().isVisible());
  check("sidebar 任务列表标题", await page.getByText("任务列表", { exact: true }).first().isVisible());
  check("旧导航「选品」已移除", !(await page.getByRole("button", { name: /^选品$/ }).first().isVisible().catch(() => false)));

  // ② 斜杠命令面板：hero 可见 → 在 composer 输入 "/"
  // 给 React 一些时间水合 + 渲染首屏（多次 HMR 后 dev 首屏可能慢）。
  await page.locator("textarea").first().waitFor({ timeout: 10000 });
  await page.waitForTimeout(800);
  await page.locator("textarea").first().click();
  await page.locator("textarea").first().fill("/");
  await page.waitForTimeout(800);
  const paletteVisible = await page.getByText("/选品 <需求描述>").first().isVisible().catch(() => false);
  check("输入 / 弹出命令面板（含 /选品 usage）", paletteVisible);
  await page.screenshot({ path: "docs/screenshots/p12-01-command-palette.png", fullPage: false });

  // ③ 选中命令 → query 规范化为 /选品 前缀
  await page.getByText("/选品 <需求描述>").first().click();
  await page.waitForTimeout(200);
  const q = await page.locator("textarea").first().inputValue();
  check("选中后 query 前缀规范化", q.startsWith("/选品"), `query="${q}"`);
  await page.locator("textarea").first().fill("");

  // ④ listings 视图：点击侧栏入口 → 标题 + 空态/列表
  await page.getByText("Listing 列表", { exact: true }).first().click();
  await page.waitForTimeout(500);
  const heading = await page.getByRole("heading", { name: "Listing 列表" }).first().isVisible().catch(() => false);
  check("listings 视图打开", heading);
  await page.screenshot({ path: "docs/screenshots/p12-02-listings-view.png", fullPage: false });

  // ⑤ 配置页：已配置供应商汇总条（有 saved 时才渲染；这里至少验证页面可用 + Amazon 卡全宽类名）
  await page.getByText("LLM 配置", { exact: true }).first().click();
  // dev 冷编译下 /api/selection/config 系列首载可达 7s+，等 heading 真出现
  await page.getByRole("heading", { name: "LLM 配置" }).first().waitFor({ timeout: 20000 });
  check("配置页打开", await page.getByRole("heading", { name: "LLM 配置" }).first().isVisible().catch(() => false));
  const summary = await page.getByText(/已配置供应商/).first().isVisible().catch(() => false);
  check("已配置供应商汇总条（有配置时渲染）", summary, summary ? "visible" : "本环境无 saved 供应商，跳过渲染属预期");
  const amazonW = await page.getByText("Amazon 上传通道").first().isVisible().catch(() => false);
  check("Amazon 分区存在", amazonW);
  await page.screenshot({ path: "docs/screenshots/p12-03-config.png", fullPage: false });

  // ⑥ 回到对话：新建任务 → chat 视图 + composer 回来
  await newTaskBtn.first().click();
  await page.waitForTimeout(300);
  check("新建任务回到对话视图", await page.locator("textarea").first().isVisible());

  // ⑦ P13 偏好迁移：composer 不再有 评分参考 chip；侧栏 偏好 入口可达
  const oldChips = await page.getByText("评分参考").count();
  check("composer 评分参考 chips 已移除", oldChips === 0, `count=${oldChips}`);
  const prefBtn = page.getByRole("button", { name: /偏好/ });
  check("侧栏 偏好 入口可达", await prefBtn.first().isVisible());
  await prefBtn.first().click();
  await page.waitForTimeout(800);
  check("偏好视图打开", await page.getByRole("heading", { name: "偏好" }).first().isVisible().catch(() => false));
  // 清除全部迁移：偏好页底部单一红色「删除所有记忆」按钮 + Modal 二次确认
  const redDeleteBtn = page.getByRole("button", { name: "删除所有记忆" });
  check("偏好页含红色「删除所有记忆」按钮", await redDeleteBtn.first().isVisible());
  check(
    "侧栏已无「清除全部记忆」按钮",
    !(await page.getByText("清除全部记忆", { exact: true }).first().isVisible().catch(() => false)),
  );
  check(
    "无「危险操作」区块标题",
    !(await page.getByText("危险操作").first().isVisible().catch(() => false)),
  );
  // 点击应弹出 Modal 显示「此操作不可撤销。」
  await redDeleteBtn.first().click();
  await page.waitForTimeout(300);
  check(
    "Modal 显示「此操作不可撤销」",
    await page.getByText("此操作不可撤销").first().isVisible(),
  );
  await page.screenshot({ path: "docs/screenshots/p12-04-preferences.png", fullPage: false });
  // 关闭 modal 不执行删除
  await page.getByRole("button", { name: "取消" }).first().click();
  await page.waitForTimeout(200);

  // ⑧ Tab 选中斜杠命令：composer 输入 / 后按 Tab，应直接选中第一项并写入 /选品 前缀
  await newTaskBtn.first().click();
  await page.waitForTimeout(300);
  const ta = page.locator("textarea").first();
  await ta.click();
  await ta.fill("/");
  await page.waitForTimeout(200);
  const paletteVisible2 = await page.getByText("/选品 <需求描述>").first().isVisible().catch(() => false);
  if (paletteVisible2) {
    await page.keyboard.press("Tab");
    await page.waitForTimeout(200);
    const q = await ta.inputValue();
    check("Tab 选中面板高亮命令", q.startsWith("/选品"), `query="${q}"`);
  } else {
    check("Tab 选中面板高亮命令", false, "面板未出现，跳过");
  }

  check("无页面运行时错误", errors.length === 0, errors.slice(0, 2).join(" | "));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
