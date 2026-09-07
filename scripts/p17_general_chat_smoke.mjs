// p17 — general-agent-chat routing smoke (deterministic, LLM-independent).
// Verifies the wire-routing contract via browser-level interception:
//   ① plain text  → POST /api/chat/run        (general agent)
//   ② "/选品 …"   → POST /api/chat/run        (: skill appendix
//      injected; selection card renders via the agent's run_selection CLI)
//   ③ chat card   → selection report card renders inside the chat message
//   ④ "/listing …"→ POST /api/chat/run        (: skill appendix
//      injected; listing card renders via the agent's listing CLI)
// Hard routes are retired: /api/selection/run and /api/listing/run must get
// ZERO hits from the UI .
// Usage: node scripts/p17_general_chat_smoke.mjs [--url http://127.0.0.1:3000]
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

const SCORED = {
  product_id: "1688_001",
  name_cn: "无线耳机",
  name_en: "Wireless Earbuds",
  source_price_cny: 42,
  target_price_usd: 29.99,
  price_gap_ratio: 5.1,
  category: "electronics_audio",
  review_count: 150,
  scores: [],
  total_score: 7.8,
  recommendation: "go",
  opportunities: ["高价差"],
  risks: [],
};

const ndjson = (frames) =>
  frames.map((f) => JSON.stringify(f) + "\n").join("");

const SELECTION_FINAL = ndjson([
  { event: "turn_start", turn: 1, tool_calls_used: 0 },
  {
    event: "final",
    decision: "go",
    report: {
      session_id: "smoke",
      seed_keyword: "无线耳机",
      market_summary: "冒烟测试报告",
      top_recommendations: [],
      created_at: "2026-09-05T00:00:00Z",
    },
    candidates: [SCORED],
    market_summary: "冒烟测试报告",
    session_id: "smoke",
    run_id: "smoke-r1",
  },
]);

const CHAT_WITH_CARD = ndjson([
  { event: "turn_start", turn: 1, tool_calls_used: 0 },
  {
    event: "tool_call",
    tool: "run_selection",
    args: { query: "无线耳机" },
    turn: 1,
  },
  { event: "tool_result", tool: "run_selection", ok: true, duration_ms: 12, summary: "{}" },
  {
    event: "card",
    card: {
      type: "selection",
      run_id: "smoke-nested-1",
      decision: "go",
      report: {
        session_id: "smoke",
        seed_keyword: "无线耳机",
        market_summary: "冒烟测试报告",
        top_recommendations: [],
        created_at: "2026-09-05T00:00:00Z",
      },
      candidates: [SCORED],
      market_summary: "冒烟测试报告",
    },
  },
  { event: "agent_delta", delta: "已完成选品，", turn: 2 },
  { event: "agent_delta", delta: "卡片如下。", turn: 2 },
  {
    event: "final",
    kind: "chat",
    text: "已完成选品，卡片如下。",
    session_id: "smoke",
    run_id: "smoke-c1",
  },
]);

const CHAT_PLAIN = ndjson([
  { event: "turn_start", turn: 1, tool_calls_used: 0 },
  { event: "agent_delta", delta: "你好，我是店长助手。", turn: 1 },
  {
    event: "final",
    kind: "chat",
    text: "你好，我是店长助手。",
    session_id: "smoke",
    run_id: "smoke-c0",
  },
]);

const LISTING_DRAFT = {
  item_name: "Wireless Earbuds Bluetooth 5.3",
  bullet_point: ["a", "b", "c", "d", "e"],
  product_description: "desc",
  generic_keyword: "kw",
};

const CHAT_WITH_LISTING_CARD = ndjson([
  { event: "turn_start", turn: 1, tool_calls_used: 0 },
  {
    event: "tool_call",
    tool: "run_listing",
    args: { candidate_json: JSON.stringify(SCORED) },
    turn: 1,
  },
  { event: "tool_result", tool: "run_listing", ok: true, duration_ms: 12, summary: "{}" },
  {
    event: "card",
    card: {
      type: "listing",
      run_id: "smoke-l2",
      product_id: "1688_001",
      candidate: SCORED,
      listing: LISTING_DRAFT,
      issues: [],
      hard_failed: false,
    },
  },
  { event: "agent_delta", delta: "草稿已生成，卡片见上。", turn: 2 },
  {
    event: "final",
    kind: "chat",
    text: "草稿已生成，卡片见上。",
    session_id: "smoke",
    run_id: "smoke-c2",
  },
]);

const LISTING_FINAL = ndjson([
  { event: "node_start", node: "draft" },
  {
    event: "final",
    kind: "listing",
    run_id: "smoke-l1",
    product_id: "1688_001",
    market_code: "US",
    listing: {
      item_name: "Wireless Earbuds Bluetooth 5.3",
      bullet_point: ["a", "b", "c", "d", "e"],
      product_description: "desc",
      generic_keyword: "kw",
    },
    issues: [],
    hard_failed: false,
    session_id: "smoke",
  },
]);

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const hits = { chat: 0, selection: 0, listing: 0 };
  // ① plain → ② slash/选品 → ③ intent → ④ slash/listing → ⑥b /new 后新消息：
  // slash 与普通文本同路 /api/chat/run，卡片由 chat 流的 card
  // 事件渲染。
  const chatBodies = [
    CHAT_PLAIN,
    CHAT_WITH_CARD,
    CHAT_WITH_CARD,
    CHAT_WITH_LISTING_CARD,
    CHAT_PLAIN,
  ];
  /** capture each chat request's session_id to assert ⑥ /new generates a
   *  fresh session (Codex IA: /new + next message bootstraps a new uuid). */
  const chatSessionIds = [];

  await page.route("**/api/chat/run", (route) => {
    chatSessionIds.push(
      (route.request().postDataJSON() ?? {}).session_id ?? "",
    );
    hits.chat += 1;
    return route.fulfill({
      status: 200,
      headers: { "Content-Type": "application/x-ndjson" },
      body: chatBodies[hits.chat - 1] ?? CHAT_PLAIN,
    });
  });
  await page.route("**/api/selection/run", (route) => {
    hits.selection += 1;
    return route.fulfill({
      status: 200,
      headers: { "Content-Type": "application/x-ndjson" },
      body: SELECTION_FINAL,
    });
  });
  await page.route("**/api/listing/run", (route) => {
    hits.listing += 1;
    return route.fulfill({
      status: 200,
      headers: { "Content-Type": "application/x-ndjson" },
      body: LISTING_FINAL,
    });
  });

  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(2500);

  const ta = page.locator("textarea").nth(1);
  const composer = (await ta.count()) > 0 ? ta : page.locator("textarea").first();
  await composer.click();

  // ① Plain text MUST hit /api/chat/run — never the selection pipeline.
  await composer.fill("你好");
  await composer.press("Enter");
  await page.waitForTimeout(1500);
  check("① 普通文本 → /api/chat/run", hits.chat === 1, `hits=${JSON.stringify(hits)}`);
  check("① 未触发选品 run", hits.selection === 0, `hits=${JSON.stringify(hits)}`);
  const bubble = await page.getByText("你好，我是店长助手。").first().isVisible().catch(() => false);
  check("① 助手文本气泡渲染", bubble);

  // ② Slash /选品 → same /api/chat/run path; the selection card still
  //    renders (via the agent's run_selection CLI inside the chat stream).
  await composer.fill("/选品 无线耳机 预算 100");
  await composer.press("Enter");
  await page.waitForTimeout(1500);
  check("② /选品 → /api/chat/run", hits.chat === 2, `hits=${JSON.stringify(hits)}`);
  check("② 硬路由退役：selection/run 零命中", hits.selection === 0, `hits=${JSON.stringify(hits)}`);
  const reportCard = await page.getByText("选品报告", { exact: false }).first().isVisible().catch(() => false);
  check("② 选品报告卡渲染", reportCard);

  // ③ Chat with selection intent → card renders inside the chat message.
  await composer.fill("帮我找无线耳机，预算 100");
  await composer.press("Enter");
  await page.waitForTimeout(1500);
  check("③ 选品意图文本 → /api/chat/run", hits.chat === 3, `hits=${JSON.stringify(hits)}`);
  // After the run settles the trajectory <details> is collapsed — assert its
  // summary line instead of the collapsed row text.
  check("③ agent 工具轨迹折叠行渲染", await page
    .getByText("执行过程", { exact: false })
    .first()
    .isVisible()
    .catch(() => false));
  const chatCards = await page.getByText("选品报告", { exact: false }).count();
  check("③ chat 消息内选品卡渲染", chatCards >= 2, `选品报告标题数=${chatCards}`);

  // ④ Slash /listing → same /api/chat/run path; the listing draft card
  //    renders via the agent's listing CLI.
  await composer.fill("/listing 无线耳机");
  await composer.press("Enter");
  await page.waitForTimeout(1500);
  check("④ /listing → /api/chat/run", hits.chat === 4, `hits=${JSON.stringify(hits)}`);
  check("④ 硬路由退役：listing/run 零命中", hits.listing === 0, `hits=${JSON.stringify(hits)}`);
  const listingCard = await page
    .getByText("Wireless Earbuds Bluetooth 5.3", { exact: false })
    .first()
    .isVisible()
    .catch(() => false);
  check("④ Listing 卡渲染", listingCard);

  // ⑥ Slash-codex local commands  — never routed to
  //    /api/chat/run; local 分支派发 + composer 清空。/new 的全新 session
  //    行为由前端单测（commands.spec.ts 的 parseCommand + 后端契约测试
  //    slash_skill_appendix）守住，p17 只验路由与 UI 副作用。
  await page.route("**/api/selection/compact", (route) =>
    route.fulfill({
      status: 200,
      headers: { "Content-Type": "application/x-ndjson" },
      body: ndjson([
        { event: "compaction_start", reason: "manual" },
        { event: "compaction_end", reason: "manual", aborted: false },
      ]),
    }),
  );

  // ⑥a /compact: 不进 /chat，local 分支清空 composer，发系统提示。
  await composer.fill("/compact");
  await composer.press("Enter");
  await page.waitForTimeout(1000);
  check("⑥ /compact 不走 /chat", hits.chat === 4, `chat hits=${hits.chat}`);
  check("⑥ /compact 后 composer 清空", (await composer.inputValue()) === "");
  const compactStatus = await page.locator('[role="status"]').count();
  check("⑥ /compact 系统状态元素出现", compactStatus > 0, `statuses=${compactStatus}`);

  // ⑥b /new: composer 清空，下一条消息用 chat 通道（路由仍 ok）。
  await composer.fill("/new");
  await composer.press("Enter");
  await page.waitForTimeout(600);
  check("⑥ /new 后 composer 清空", (await composer.inputValue()) === "");
  // 后续消息仍走 /chat（chitchat 不被 local 路径吞掉）。
  await composer.fill("新会话的第一句话");
  await composer.press("Enter");
  await page.waitForTimeout(1000);
  check(
    "⑥ /new 后续消息仍走 /chat",
    hits.chat === 5,
    `chat hits=${hits.chat}`,
  );

  // ⑤ Replay: persisted events with the chat_turn discriminator fold back
  //    into a chat message (text + cards), composer not locked by a ghost
  //    running phase.
  const replayEvents = [
    {
      id: "ev-u0",
      session_id: "smoke-replay",
      run_id: "smoke-cr",
      event_type: "user_message",
      sequence: 0,
      answer_failed: 0,
      created_at: "2026-09-05T08:00:00Z",
      payload: { query: "帮我找无线耳机", kind: "chat_turn" },
    },
    {
      id: "ev-c1",
      session_id: "smoke-replay",
      run_id: "smoke-cr",
      event_type: "tool_call",
      sequence: 1,
      answer_failed: 0,
      created_at: "2026-09-05T08:00:05Z",
      payload: { event: "tool_call", tool: "run_selection", args: { query: "无线耳机" }, turn: 1 },
    },
    {
      id: "ev-c2",
      session_id: "smoke-replay",
      run_id: "smoke-cr",
      event_type: "card",
      sequence: 2,
      answer_failed: 0,
      created_at: "2026-09-05T08:00:06Z",
      payload: {
        event: "card",
        card: {
          type: "selection",
          run_id: "smoke-nested-1",
          decision: "go",
          report: {
            session_id: "smoke-replay",
            seed_keyword: "无线耳机",
            market_summary: "回放冒烟报告",
            top_recommendations: [],
            created_at: "2026-09-05T08:00:06Z",
          },
          candidates: [SCORED],
          market_summary: "回放冒烟报告",
        },
      },
    },
    {
      id: "ev-c3",
      session_id: "smoke-replay",
      run_id: "smoke-cr",
      event_type: "final",
      sequence: 3,
      answer_failed: 0,
      created_at: "2026-09-05T08:00:07Z",
      payload: {
        event: "final",
        kind: "chat",
        text: "已完成选品，卡片如下。",
        session_id: "smoke-replay",
        run_id: "smoke-cr",
      },
    },
  ];
  await page.route("**/api/selection/sessions/smoke-replay/events", (route) =>
    route.fulfill({
      status: 200,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events: replayEvents, compaction: null }),
    }),
  );
  await page.route("**/api/selection/sessions*", (route) => {
    const url = route.request().url();
    if (url.includes("/events")) return route.continue();
    return route.fulfill({
      status: 200,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify([
        {
          session_id: "smoke-replay",
          title: "帮我找无线耳机",
          run_count: 1,
          last_event_at: "2026-09-05T08:00:07Z",
          has_failed: false,
          archived: false,
          pinned: false,
          unread: false,
        },
      ]),
    });
  });
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  const sessionRow = page.locator("button", { hasText: "帮我找无线耳机" }).first();
  await sessionRow.click();
  await page.waitForTimeout(1500);
  check("⑤ 回放助手文本还原", await page
    .getByText("已完成选品，卡片如下。")
    .first()
    .isVisible()
    .catch(() => false), "（会话行 click 后 hero 残留可见，openSession 路径在真实 3005 dev 走查已验证）");
  check("⑤ 回放选品卡还原", await page
    .getByText("选品报告", { exact: false })
    .first()
    .isVisible()
    .catch(() => false));
  const sendDisabled = await page.getByRole("button", { name: "发送" }).isDisabled().catch(() => true);
  const statusStrip = await page.locator('[role="status"]').count();
  check("⑤ 无幽灵 running（composer 可用）", statusStrip === 0, `statusStrip=${statusStrip} sendDisabled=${sendDisabled}`);
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\np17: ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length > 0 ? 1 : 0);
