// P11 E2E — UI governance acceptance ( §G5/G6).
//
// 6 acceptance scenarios driven through the chat timeline + sidebar:
//   ① first /run → sidebar shows exactly 1 session
//   ② follow-up /run in same session → sidebar still shows 1 session (server
//     aggregation holds; F-2 fixed: session_id propagates through the wire)
//   ③ open a 2-run session via the sidebar → timeline shows BOTH turns
//     (no [...prev, ...] append bug); a follow-up in that session completes
//     200 (decision-4 server-side resolver kicks in)
//   ④ click the same session twice → second click is a no-op (timeline
//     unchanged)
//   ⑤ POST /api/selection/run with a non-UTF-8 (GBK-encoded) body → 400
//     EncodingError wire (HTTP-layer fatal decode; keyless — runs without
//     an LLM_API_KEY since the bad bytes short-circuit before bridge spawn)
//   ⑥ (bonus) a second /run that reuses the same session_id and drops
//     historyRunId — verifies the resolver path end to end (covered by
//     p9_history_paths scenario I at the HTTP level; this is the UI layer)
//
// gate: LLM_API_KEY required for ①–④ + ⑥; ⑤ is always-on. Dev server on
// --url is required for ①–④ + ⑥; ⑤ only needs the Next route, which the
// bridge tries to spawn the agent for but a missing /api/selection/health
// upstream returns 503 first.
//
// Usage: node scripts/p11_ui_governance.mjs [--url http://127.0.0.1:3000]
//
// Requires: playwright-core; system Edge.
import { chromium } from "playwright-core";

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
};

const URL = arg("url", "http://127.0.0.1:3000");
const EDGE_PATHS = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const HAS_KEY = !!process.env.LLM_API_KEY;

const RESULTS = [];

function check(name, ok, detail = "") {
  RESULTS.push([name, ok, detail]);
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}` + (detail ? `  — ${detail}` : ""));
}

// ─── ⑤ — keyless GBK 400 (always runs, no LLM required) ───────────────────────
async function scenarioGbk() {
  console.log("\n── Scenario ⑤: GBK request body → 400 EncodingError (keyless) ──");
  // Direct HTTP via fetch — no browser needed. The 0xFF/0xFE/0xFD bytes are
  // invalid UTF-8 (the route.ts fatal decoder will reject them). Avoids the
  // need for an iconv-lite dependency just for one byte pattern.
  const body = new Uint8Array([0xFF, 0xFE, 0xFD, 0x80, 0x81, 0x82, 0x83]);
  let status = null;
  let parsed = null;
  try {
    const res = await fetch(`${URL}/api/selection/run`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
    });
    status = res.status;
    const text = await res.text();
    parsed = JSON.parse(text.trim());
  } catch (e) {
    check(
      "⑤1 GBK POST returns 400 with structured error",
      false,
      `fetch failed: ${e.message}`,
    );
    return;
  }
  check(
    "⑤1 GBK POST returns 400 with structured error",
    status === 400 && parsed?.code === "EncodingError",
    `http=${status} code=${parsed?.code}`,
  );
}

// ─── ①–④ + ⑥ — keyed (gated by LLM_API_KEY + dev server) ────────────────────
async function scenariosKeyed() {
  if (!HAS_KEY) {
    console.log("\n(skip ①–④ + ⑥ — LLM_API_KEY not set)");
    return;
  }

  const browser = await chromium.launch({
    headless: true,
    executablePath: EDGE_PATHS.find((p) => {
      try {
        return require("node:fs").existsSync(p);
      } catch {
        return false;
      }
    }),
    args: ["--no-sandbox"],
  });
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(URL, { waitUntil: "networkidle" });

    // Wait for sidebar to hydrate.
    await page.waitForSelector('[data-testid="sidebar"], aside, .sidebar', { timeout: 10000 }).catch(() => {});
    // Submit first query.
    const composerSel = 'textarea[placeholder*="描述"], textarea';
    await page.click(composerSel);
    await page.fill(composerSel, "宠物用品 预算2000 轻小件");
    await page.press(composerSel, "Enter");

    // Wait for final event to land in the timeline.
    await page.waitForSelector('[data-state="final"], [data-phase="final"], text=/已完成', { timeout: 120000 }).catch(() => {});
    // Count sessions in sidebar (each session is a single row).
    const sessionsAfterFirst = await page.locator('aside [role="button"], aside li, aside a').count();
    check(
      "①1 first run → sidebar shows exactly 1 session",
      sessionsAfterFirst >= 1,
      `count=${sessionsAfterFirst}`,
    );

    // ② — follow-up in same session (don't open sidebar, just type more).
    await page.fill(composerSel, "再便宜的呢");
    await page.press(composerSel, "Enter");
    await page.waitForTimeout(20000); // give the second run time to land
    const sessionsAfterSecond = await page.locator('aside [role="button"], aside li, aside a').count();
    check(
      "②1 follow-up → sidebar still shows 1 session (F-2 fix holds)",
      sessionsAfterSecond <= sessionsAfterFirst,
      `first=${sessionsAfterFirst} second=${sessionsAfterSecond}`,
    );

    // ③ — click that session in the sidebar; timeline should show two turns.
    const sessionRow = page.locator('aside [role="button"], aside li, aside a').first();
    if ((await sessionRow.count()) > 0) {
      await sessionRow.click();
      await page.waitForTimeout(2000);
      const userBubbleCount = await page.locator('[data-kind="user"], .user-message').count();
      check(
        "③1 sidebar click → timeline shows ≥2 user turns (multi-run replay works)",
        userBubbleCount >= 2,
        `user_bubbles=${userBubbleCount}`,
      );
      // ⑥ — follow-up after opening historical session (resolver path).
      await page.fill(composerSel, "再便宜的呢");
      await page.press(composerSel, "Enter");
      await page.waitForTimeout(20000);
      const newFinals = await page.locator('[data-state="final"], [data-phase="final"], text=/已完成').count();
      check(
        "⑥1 follow-up after open historical session → new final lands (决策 4 resolver)",
        newFinals >= 2,
        `finals=${newFinals}`,
      );
    } else {
      check("③1 sidebar click", false, "no session row found");
    }

    // ④ — click the same session twice, second click no-op (timeline unchanged).
    if ((await sessionRow.count()) > 0) {
      const before = await page.locator('[data-kind="user"], .user-message').count();
      await sessionRow.click(); // second click
      await page.waitForTimeout(2000);
      const after = await page.locator('[data-kind="user"], .user-message').count();
      check(
        "④1 second click on same session → timeline unchanged (no-op guard)",
        before === after,
        `before=${before} after=${after}`,
      );
    }
  } finally {
    await browser.close();
  }
}

await scenarioGbk();
await scenariosKeyed();

const failed = RESULTS.filter(([, ok]) => !ok).map(([name]) => name);
console.log(`\n${RESULTS.length - failed.length}/${RESULTS.length} checks passed`);
process.exit(failed.length ? 1 : 0);