"use client";

import { useState } from "react";
import { Alert, AlertTitle, AlertDescription } from "@appica/ui-react/alert";
import { Button } from "@appica/ui-react/button";
import { Card } from "@appica/ui-react/card";

interface ErrorViewProps {
  query: string;
  code: string;
  message: string;
  status?: number | null;
  missing?: string[];
  onRetry: () => void;
  onBack: () => void;
  onGoConfig: () => void;
}

const MESSAGE_PREVIEW = 240;

/** Best-effort actionable advice per wire error code. aimux never exposes the
 * upstream HTTP status on the wire, so 401/429 hints read the message text —
 * classification only changes the suggestion shown, never error handling. */
function adviceFor(code: string, message: string): { title: string; lines: string[] } | null {
  if (code === "APICallError") {
    const m = message.toLowerCase();
    const lines: string[] = [];
    if (m.includes("429") || m.includes("rate limit") || m.includes("rate_limit")) {
      lines.push("上游限流（429）——稍等片刻后点「重试」即可，配置无需改动。");
    } else if (m.includes("401") || m.includes("api key") || m.includes("incorrect")) {
      lines.push("上游拒绝了 API Key（401）——到配置页核对供应商 Key 后保存（热重载），再回输入页重试。");
    } else {
      lines.push("上游模型服务返回错误——到配置页用「连接测试」验证供应商配置，或稍后重试。");
    }
    lines.push("错误已按原样透传，未做重试或降级（设计决策）。");
    return { title: "建议操作", lines };
  }
  if (code === "NetworkError") {
    return {
      title: "建议操作",
      lines: [
        "选品服务（Python 桥）可能未启动或已中断——刷新页面会自动重新拉起；若持续失败，检查终端里的服务日志。",
      ],
    };
  }
  return null;
}

export function ErrorView({
  query,
  code,
  message,
  status,
  missing,
  onRetry,
  onBack,
  onGoConfig,
}: ErrorViewProps) {
  const [expanded, setExpanded] = useState(false);
  const advice = adviceFor(code, message);
  const isLong = message.length > MESSAGE_PREVIEW;

  return (
    <div className="mx-auto max-w-2xl px-6 py-12">
      <Card frame={false} className="bg-card p-6 [--card-radius:var(--radius-md)]">
        <Alert variant={code === "Empty" ? "default" : "error"} className="mb-4">
          <AlertTitle className="font-semibold">
            {code === "StartupConfigError"
              ? "未配置 LLM 服务"
              : code === "Empty"
                ? "没有找到候选商品"
                : code === "APICallError"
                  ? "上游模型服务错误"
                  : code === "NetworkError"
                    ? "服务连接失败"
                    : "执行出错"}
          </AlertTitle>
          <AlertDescription className="text-sm leading-relaxed">
            {isLong && !expanded
              ? `${message.slice(0, MESSAGE_PREVIEW)}…`
              : message}
            {isLong && (
              <button
                onClick={() => setExpanded((v) => !v)}
                className="ml-1 text-xs text-accent underline-offset-2 hover:underline"
              >
                {expanded ? "收起详情" : "查看完整错误"}
              </button>
            )}
          </AlertDescription>
        </Alert>

        {advice && (
          <div className="mb-4 rounded-lg bg-background-muted p-4 text-xs leading-relaxed text-foreground-muted">
            <p className="mb-2 font-semibold text-foreground">{advice.title}</p>
            <ul className="space-y-1">
              {advice.lines.map((line, i) => (
                <li key={i}>· {line}</li>
              ))}
            </ul>
          </div>
        )}

        <dl className="mb-6 grid gap-3 text-xs">
          <div className="flex justify-between gap-4">
            <dt className="text-foreground-subtle">查询</dt>
            <dd className="text-foreground">「{query}」</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-foreground-subtle">错误码</dt>
            <dd className="font-mono text-foreground">{code}</dd>
          </div>
          {status != null && (
            <div className="flex justify-between gap-4">
              <dt className="text-foreground-subtle">HTTP 状态</dt>
              <dd className="font-mono text-foreground">{status}</dd>
            </div>
          )}
          {missing && missing.length > 0 && (
            <div>
              <dt className="mb-1 text-foreground-subtle">缺失配置</dt>
              <dd className="flex flex-wrap gap-2">
                {missing.map((m) => (
                  <code
                    key={m}
                    className="rounded bg-background-muted px-2 py-1 text-foreground"
                  >
                    {m}
                  </code>
                ))}
              </dd>
            </div>
          )}
        </dl>

        {code === "StartupConfigError" && (
          <div className="mb-6 rounded-lg bg-background-muted p-4 text-xs leading-relaxed text-foreground-muted">
            <p className="mb-2 font-semibold text-foreground">
              到配置页添加供应商 API Key 并保存即可——保存后模型立即热重载，
              无需重启服务。回到输入页重新发起选品就能继续。
            </p>
            <p>
              也可以在 <code>.env.local</code> 中补齐以下字段后重启服务：
            </p>
            <pre className="mt-2 overflow-x-auto font-mono text-[11px]">
{`LLM_API_KEY=sk-...
LLM_PRIMARY_PROVIDER=deepseek
LLM_PRIMARY_MODEL=deepseek-chat
LLM_CHEAP_PROVIDER=deepseek
LLM_CHEAP_MODEL=deepseek-chat`}
            </pre>
          </div>
        )}

        <div className="flex gap-3">
          {code === "Empty" ? (
            // Retrying the same query hits the same empty dataset — the
            // productive action is a different keyword.
            <Button
              className="rounded-md bg-accent px-6 text-accent-contrast hover:bg-accent-hover"
              onClick={onBack}
            >
              换个关键词 →
            </Button>
          ) : (
            <Button
              variant="secondary"
              onClick={onBack}
              className="rounded-sm bg-background-muted"
            >
              ← 返回输入
            </Button>
          )}
          {code === "StartupConfigError" ? (
            // Retry is pointless until the config changes — route to the
            // config page instead of offering a dead button.
            <Button
              className="rounded-md bg-accent px-6 text-accent-contrast hover:bg-accent-hover"
              onClick={onGoConfig}
            >
              去配置页 →
            </Button>
          ) : code !== "Empty" ? (
            <Button
              className="rounded-md bg-accent px-6 text-accent-contrast hover:bg-accent-hover"
              onClick={onRetry}
            >
              重试
            </Button>
          ) : null}
          {code === "APICallError" && (
            <Button
              variant="secondary"
              onClick={onGoConfig}
              className="rounded-sm bg-background-muted"
            >
              去配置页
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
}