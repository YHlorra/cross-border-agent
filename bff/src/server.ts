/**
 * BFF 服务:Hono + @hono/node-server;生产托管 workbench/dist(SPA fallback)。
 */
import { serve } from "@hono/node-server";
import { serveStatic } from "@hono/node-server/serve-static";
import { Hono } from "hono";
import fs from "node:fs";
import path from "node:path";
import routes from "./routes.js";

const PORT = Number(process.env.BFF_PORT ?? 3000);
const DIST = path.resolve(
  process.env.WORKBENCH_DIST ??
    (fs.existsSync(path.join(process.cwd(), "workbench", "dist"))
      ? path.join(process.cwd(), "workbench", "dist")
      : path.join(process.cwd(), "..", "workbench", "dist")),
);

const app = new Hono();
app.route("/", routes);

// 静态托管 + SPA fallback(生产)
if (fs.existsSync(DIST)) {
  app.use("*", serveStatic({ root: path.relative(process.cwd(), DIST) }));
  app.get("*", (c) => {
    const index = path.join(DIST, "index.html");
    if (fs.existsSync(index)) {
      return c.html(fs.readFileSync(index, "utf-8"));
    }
    return c.notFound();
  });
} else {
  app.get("/", (c) => c.text("bff: workbench/dist not built (dev 用 vite:5173)", 200));
}

serve({ fetch: app.fetch, port: PORT, hostname: "127.0.0.1" }, (info) => {
  console.log(`[bff] listening on http://127.0.0.1:${info.port}`);
});
