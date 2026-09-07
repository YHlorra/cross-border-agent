import { describe, expect, it } from "vitest";
import { Hono } from "hono";
import { fatalUtf8Body } from "../src/proxy.js";
import { z } from "zod";

const app = new Hono();
app.use("/api/*", fatalUtf8Body);
app.post("/api/echo", async (c: any) => {
  const body = c.get("utf8Body");
  return c.body(new TextDecoder().decode(body ?? new Uint8Array()));
});
app.post(
  "/api/strict",
  async (c: any) => {
    const body = c.get("utf8Body");
    let parsed: any;
    try {
      parsed = z
        .object({ query: z.string() })
        .strict()
        .parse(JSON.parse(new TextDecoder().decode(body ?? new Uint8Array())));
    } catch {
      return c.json({ error: "ValidationError", status: 400 }, 400);
    }
    return c.json({ ok: true, query: parsed.query });
  },
);

const enc = (s: string) => new TextEncoder().encode(s);

describe("fatal-UTF-8 门", () => {
  it("合法 UTF-8 体透传", async () => {
    const res = await app.request("/api/echo", {
      method: "POST",
      body: enc('{"query":"宠物用品"}'),
    });
    expect(res.status).toBe(200);
    expect(await res.text()).toBe('{"query":"宠物用品"}');
  });

  it("非法 UTF-8 字节 → 400 EncodingError", async () => {
    const bad = new Uint8Array([0x7b, 0x22, 0x81, 0x22, 0x7d]); // {",0x81,"}
    const res = await app.request("/api/echo", {
      method: "POST",
      body: bad,
    });
    expect(res.status).toBe(400);
    const body = (await res.json()) as any;
    expect(body.error).toBe("EncodingError");
  });
});

describe("Zod strict 校验", () => {
  it("未知字段(extra)→ 400", async () => {
    const res = await app.request("/api/strict", {
      method: "POST",
      body: enc('{"query":"q","bogus":1}'),
    });
    expect(res.status).toBe(400);
  });

  it("合法体通过", async () => {
    const res = await app.request("/api/strict", {
      method: "POST",
      body: enc('{"query":"q"}'),
    });
    expect(res.status).toBe(200);
    expect(((await res.json()) as any).query).toBe("q");
  });
});
