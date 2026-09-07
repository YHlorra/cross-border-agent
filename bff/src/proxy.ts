/**
 * fatal-UTF-8 body 门 + 代理(Hono 版,复刻原 Next 代理语义):
 * 非法 UTF-8 请求体在边界被 400 拒绝,不触达 Python。
 */
import type { MiddlewareHandler } from "hono";
import { proxyJson, proxyNdjson } from "./agent-bridge.js";

type Env = { Variables: { utf8Body: Uint8Array } };
export type EnvType = Env;

export const fatalUtf8Body: MiddlewareHandler<Env> = async (c, next) => {
  if (["POST", "PUT", "PATCH"].includes(c.req.method)) {
    const buf = new Uint8Array(await c.req.arrayBuffer());
    try {
      new TextDecoder("utf-8", { fatal: true }).decode(buf);
    } catch {
      return c.json(
        {
          error: "EncodingError",
          message: "request body is not valid UTF-8",
          status: 400,
        },
        400,
      );
    }
    c.set("utf8Body", buf);
  }
  await next();
};

/** 从缓存体(或查询串)构造上游转发 init。 */
export function buildInit(
  c: { req: { method: string; raw: Request; query: () => Record<string, string> } },
  body?: Uint8Array,
): RequestInit {
  const init: RequestInit = { method: c.req.method };
  if (body !== undefined && body.length > 0) {
    init.body = body as unknown as any;
    (init.headers as Record<string, string>) = {
      "content-type": "application/json",
      "content-length": String(body.byteLength),
    };
  }
  // 浏览器断开/点停止 → 取消链贯通到 Python
  init.signal = c.req.raw.signal;
  return init;
}

export async function passthroughJson(
  c: { req: any; json: (o: any, s?: number) => Response },
  upstreamPath: string,
  body?: Uint8Array,
): Promise<Response> {
  const upstream = await proxyJson(upstreamPath, buildInit(c, body));
  const headers: Record<string, string> = {};
  upstream.headers.forEach((v, k) => (headers[k] = v));
  return new Response(upstream.body, {
    status: upstream.status,
    headers,
  });
}

export async function passthroughNdjson(
  c: { req: any; json: (o: any, s?: number) => Response },
  upstreamPath: string,
  body: Uint8Array,
): Promise<Response> {
  const upstream = await proxyNdjson(upstreamPath, buildInit(c, body));
  const headers: Record<string, string> = {};
  upstream.headers.forEach((v, k) => (headers[k] = v));
  return new Response(upstream.body, {
    status: upstream.status,
    headers,
  });
}
