/**
 * BFF 路由表 — 与原 app/api/** 27 条路由逐一对应(同路径/方法/上游)。
 * Zod strict 校验对应 Pydantic extra="forbid";NDJSON 走 passthroughNdjson
 * 并透传取消链。
 */
import { Hono } from "hono";
import { z } from "zod";
import type { EnvType as Env } from "./proxy.js";
import { fatalUtf8Body, passthroughJson, passthroughNdjson } from "./proxy.js";

type C = { req: any; json: (o: any, s?: number) => Response };
type Upstream = string | ((c: any) => string);

const app = new Hono<Env>();
app.use("/api/*", fatalUtf8Body);

function bodyText(c: any): Uint8Array | undefined {
  const b = c.get("utf8Body") as Uint8Array | undefined;
  return b;
}

function resolve(c: any, up: Upstream): string {
  return typeof up === "function" ? up(c) : up;
}

function qs(c: any): string {
  const q: Record<string, string> = c.req.query();
  const entries = Object.entries(q);
  if (entries.length === 0) return "";
  return "?" + new URLSearchParams(entries).toString();
}

function get(path: string, upstream: Upstream): void {
  app.get(path, async (c: any) =>
    passthroughJson(c, resolve(c, upstream) + qs(c)),
  );
}

function del(path: string, upstream: Upstream): void {
  app.delete(path, async (c: any) => passthroughJson(c, resolve(c, upstream)));
}

function patch(
  path: string,
  upstream: Upstream,
  schema?: z.ZodTypeAny,
): void {
  app.patch(path, async (c: any) => {
    const body = bodyText(c);
    if (schema) {
      try {
        schema.parse(JSON.parse(new TextDecoder().decode(body ?? new Uint8Array())));
      } catch (e: any) {
        return c.json(
          {
            event: "error",
            code: "BadRequest",
            message: e?.issues
              ? e.issues
                  .map((i: any) => `${i.path.join(".") || "<root>"}: ${i.message}`)
                  .join("; ")
              : e?.message ?? "invalid body",
            status: 400,
          },
          400,
        );
      }
    }
    return passthroughJson(c, resolve(c, upstream), body);
  });
}

function post(path: string, upstream: Upstream, schema?: z.ZodTypeAny): void {
  app.post(path, async (c: any) => {
    const body = bodyText(c);
    if (schema) {
      try {
        schema.parse(JSON.parse(new TextDecoder().decode(body ?? new Uint8Array())));
      } catch (e: any) {
        return c.json(
          {
            error: "ValidationError",
            message: String(e?.message ?? e).slice(0, 300),
            status: 400,
          },
          400,
        );
      }
    }
    return passthroughJson(c, resolve(c, upstream), body);
  });
}

function ndjson(path: string, upstream: Upstream): void {
  app.post(path, async (c: any) =>
    passthroughNdjson(c, resolve(c, upstream), bodyText(c) ?? new Uint8Array()),
  );
}

// ── chat ──
ndjson("/api/chat/run", "/chat");

// ── listing ──
get("/api/listing/channel", "/listing/channel");
post("/api/listing/export", "/listing/export");
post("/api/listing/regen-field", "/listing/regen-field");
ndjson("/api/listing/run", "/listing/run");
get("/api/listing/runs", "/listing/runs");
app.get("/api/listing/runs/:run_id", (c: any) =>
  passthroughJson(c, `/listing/runs/${c.req.param("run_id")}`),
);
app.delete("/api/listing/runs/:run_id", (c: any) =>
  passthroughJson(c, `/listing/runs/${c.req.param("run_id")}`),
);
app.patch("/api/listing/runs/:run_id", (c: any) =>
  passthroughJson(c, `/listing/runs/${c.req.param("run_id")}`, bodyText(c)),
);
post("/api/listing/upload", "/listing/upload");
app.patch("/api/listing/runs/:run_id/fields", (c: any) =>
  passthroughJson(
    c,
    `/listing/runs/${c.req.param("run_id")}/fields`,
    bodyText(c),
  ),
);
post("/api/listing/gen-image", "/listing/gen-image");

// ── preferences ──
get("/api/preferences", "/preferences");
post(
  "/api/preferences",
  "/preferences",
  z.object({ text: z.string().min(1), category: z.string().optional() }).strict(),
);
app.delete("/api/preferences/:id", (c: any) =>
  passthroughJson(c, `/preferences/${c.req.param("id")}`),
);
patch(
  "/api/preferences/:id",
  (c: any) => `/preferences/${c.req.param("id")}`,
  z
    .object({ text: z.string().min(1).optional(), category: z.string().optional() })
    .strict(),
);

// ── selection ──
ndjson("/api/selection/compact", "/compact");
get("/api/selection/config", "/config");
app.get("/api/selection/config/model", (c: any) =>
  passthroughJson(c, "/config/model"),
);
post("/api/selection/config/model", "/config/model");
post("/api/selection/config/save", "/config/save");
get("/api/selection/config/saved", "/config/saved");
post("/api/selection/config/saved", "/config/saved");
post("/api/selection/config/saved/delete", "/config/saved/delete");
app.get("/api/selection/config/spapi", (c: any) =>
  passthroughJson(c, "/config/spapi"),
);
post("/api/selection/config/spapi", "/config/spapi");
get("/api/selection/health", "/healthz");
get("/api/selection/history", "/history");
app.get("/api/selection/history/:run_id", (c: any) =>
  passthroughJson(c, `/history/${c.req.param("run_id")}`),
);
post("/api/selection/models", "/config/models");
post("/api/selection/providers", "/config/providers");
ndjson("/api/selection/run", "/run");
get("/api/selection/sessions", "/sessions");
app.delete("/api/selection/sessions/:session_id", (c: any) =>
  passthroughJson(c, `/sessions/${c.req.param("session_id")}`),
);
app.patch("/api/selection/sessions/:session_id", (c: any) =>
  passthroughJson(c, `/sessions/${c.req.param("session_id")}`, bodyText(c)),
);
app.get("/api/selection/sessions/:session_id/events", (c: any) =>
  passthroughJson(
    c,
    `/sessions/${c.req.param("session_id")}/events` + qs(c),
  ),
);
post("/api/selection/test", "/config/test");

export default app;
