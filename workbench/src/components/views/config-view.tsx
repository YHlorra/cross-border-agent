"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@appica/ui-react/button";
import { Card } from "@appica/ui-react/card";
import { Input } from "@appica/ui-react/input";
import { Spinner } from "@appica/ui-react/spinner";
import { Badge } from "@appica/ui-react/badge";
import { Alert } from "@appica/ui-react/alert";
import { Skeleton } from "@appica/ui-react/skeleton";
import { Field, FieldDescription, FieldLabel } from "@appica/ui-react/field";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@appica/ui-react/collapsible";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@appica/ui-react/tabs";
import { Separator } from "@appica/ui-react/separator";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@appica/ui-react/dialog";

import {
  deleteSavedProvider,
  fetchModelConfig,
  fetchModels,
  fetchProviders,
  fetchSavedProviders,
  saveModelConfig,
  saveProvider,
  testConnection,
} from "@/lib/selection-api";
import { ConfigApiError } from "@/lib/config-types";
import {
  AGENT_LABELS,
  AGENT_ORDER,
} from "@/lib/config-types";
import type {
  ConfigSnapshot,
  ModelConfig,
  ModelOption,
  ModelRef,
  ProviderOption,
  SavedProvider,
} from "@/lib/config-types";
import {
  fetchListingChannel,
  fetchSpapiConfig,
  saveSpapiConfig,
} from "@/lib/listing-api";

interface ConfigViewProps {
  snapshot: ConfigSnapshot | null;
  onSaved: (snapshot: ConfigSnapshot) => void;
}

const EMPTY_CONFIG: ModelConfig = {
  default: { provider: null, model: null, base_url: null },
  agents: Object.fromEntries(AGENT_ORDER.map((k) => [k, null])),
};

export function ConfigView({ onSaved }: ConfigViewProps) {
  // ── data ────────────────────────────────────────────────────────────────
  const [providers, setProviders] = useState<ProviderOption[]>([]);
  const [saved, setSaved] = useState<SavedProvider[]>([]);
  const [modelCfg, setModelCfg] = useState<ModelConfig>(EMPTY_CONFIG);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busyName, setBusyName] = useState<string | null>(null);

  // preset keys
  const [keys, setKeys] = useState<Record<string, string>>({});

  // custom form
  const [custom, setCustom] = useState({ name: "", baseUrl: "", apiKey: "" });
  const [customTest, setCustomTest] = useState<string | null>(null);

  // preset search
  const [presetQuery, setPresetQuery] = useState("");

  // model picker
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerAgent, setPickerAgent] = useState<string | null>(null);
  const [pickerChoice, setPickerChoice] = useState<string>("inherit");

  const savedMap = useMemo(
    () => new Map(saved.map((s) => [s.name, s])),
    [saved],
  );

  const filteredProviders = useMemo(() => {
    const q = presetQuery.trim().toLowerCase();
    if (!q) return providers;
    return providers.filter(
      (p) =>
        p.label.toLowerCase().includes(q) ||
        p.name.toLowerCase().includes(q) ||
        (p.url ?? "").toLowerCase().includes(q),
    );
  }, [providers, presetQuery]);

  const refresh = useCallback(async () => {
    try {
      const [list, savedList, cfg] = await Promise.all([
        fetchProviders(),
        fetchSavedProviders(),
        fetchModelConfig(),
      ]);
      setProviders(list);
      setSaved(savedList);
      setModelCfg(cfg);
    } catch (e: unknown) {
      setError(formatError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // ── effective model resolution ──────────────────────────────────────────
  const effectiveModel = useCallback(
    (agentKey: string): ModelRef => {
      const override = modelCfg.agents[agentKey];
      if (override?.provider && override.model) return override;
      return modelCfg.default;
    },
    [modelCfg],
  );

  const defaultLabel = modelCfg.default.provider
    ? `${providerLabel(modelCfg.default.provider)} ${modelCfg.default.model}`
    : "未配置";

  const optionsByProvider = useMemo(() => {
    const groups: { provider: SavedProvider; models: ModelOption[] }[] = [];
    for (const p of saved) {
      if (p.models.length > 0) groups.push({ provider: p, models: p.models });
    }
    return groups;
  }, [saved]);

  // ── model picker ────────────────────────────────────────────────────────
  const DEFAULT_KEY = "__default__";

  const openPicker = useCallback((agentKey: string) => {
    setPickerAgent(agentKey);
    setPickerChoice(
      agentKey === DEFAULT_KEY
        ? `${modelCfg.default.provider ?? ""}:${modelCfg.default.model ?? ""}`
        : "inherit",
    );
    setPickerOpen(true);
  }, [modelCfg.default.provider, modelCfg.default.model]);

  const pickerTitle = pickerAgent
    ? pickerAgent === DEFAULT_KEY
      ? "为「全局默认模型」选择模型"
      : `为「${AGENT_LABELS[pickerAgent] ?? pickerAgent}」选择模型`
    : "";

  const confirmPicker = useCallback(async () => {
    if (!pickerAgent) return;

    setBusyName("__model__");
    setError(null);
    try {
      const next: ModelConfig = {
        default: modelCfg.default,
        agents: { ...modelCfg.agents },
      };
      if (pickerAgent === DEFAULT_KEY) {
        if (pickerChoice) {
          const [provider, model] = pickerChoice.split(":", 2);
          next.default = {
            provider,
            model,
            base_url: savedMap.get(provider)?.base_url ?? null,
          };
        }
      } else if (pickerChoice === "inherit") {
        next.agents[pickerAgent] = null;
      } else {
        const [provider, model] = pickerChoice.split(":", 2);
        next.agents[pickerAgent] = {
          provider,
          model,
          base_url: savedMap.get(provider)?.base_url ?? null,
        };
      }
      const savedCfg = await saveModelConfig(next);
      setModelCfg(savedCfg);
      setSuccess(
        pickerAgent === DEFAULT_KEY
          ? "全局默认模型已更新，未单独设置的环节将自动跟随。"
          : pickerChoice === "inherit"
            ? `${AGENT_LABELS[pickerAgent]} 已恢复继承默认模型。`
            : `${AGENT_LABELS[pickerAgent]} 已单独设置模型。`,
      );
      setPickerOpen(false);
      void onSaved({
        primary_provider: savedCfg.default.provider,
        primary_model: savedCfg.default.model,
        cheap_provider: null,
        cheap_model: null,
        base_url: savedCfg.default.base_url ?? null,
        api_key_set: true,
      });
    } catch (e: unknown) {
      setError(formatError(e));
    } finally {
      setBusyName(null);
    }
  }, [pickerAgent, pickerChoice, modelCfg, savedMap, onSaved]);

  // ── preset provider connect ─────────────────────────────────────────────
  const connectPreset = useCallback(
    async (p: ProviderOption) => {
      const key = (keys[p.name] ?? "").trim();
      if (!key) {
        setError(`请先填写 ${p.label} 的 API 密钥。`);
        return;
      }
      setBusyName(p.name);
      setError(null);
      setSuccess(null);
      try {
        let models: ModelOption[] = [];
        if (p.can_list_models) {
          models = await fetchModels(p.name, key);
        }
        await saveProvider({
          name: p.name,
          label: p.label,
          base_url: p.url ?? null,
          api_key: key,
          models,
          category: p.category,
        });
        setSuccess(`${p.label} 已连接。`);
        setKeys((prev) => ({ ...prev, [p.name]: "" }));
        await refresh();
      } catch (e: unknown) {
        setError(formatError(e));
      } finally {
        setBusyName(null);
      }
    },
    [keys, refresh],
  );

  // ── custom provider ─────────────────────────────────────────────────────
  const canSaveCustom =
    custom.name.trim() && custom.baseUrl.trim() && custom.apiKey.trim();

  const testCustom = useCallback(async () => {
    if (!custom.baseUrl.trim() || !custom.apiKey.trim()) {
      setError("请填写 URL 与 API 密钥后测试。");
      return;
    }
    setBusyName("__custom__");
    setError(null);
    setCustomTest(null);
    try {
      const res = await testConnection(
        "deepseek",
        custom.apiKey.trim(),
        "gpt-4o-mini",
        custom.baseUrl.trim(),
      );
      setCustomTest(res.reply || "连接成功，端点可访问。");
    } catch (e: unknown) {
      setError(formatError(e));
    } finally {
      setBusyName(null);
    }
  }, [custom]);

  const saveCustom = useCallback(async () => {
    if (!canSaveCustom) {
      setError("请完整填写名称、URL 与 API 密钥。");
      return;
    }
    setBusyName("__custom__");
    setError(null);
    setSuccess(null);
    try {
      await saveProvider({
        name: custom.name.trim(),
        label: custom.name.trim(),
        base_url: custom.baseUrl.trim(),
        api_key: custom.apiKey.trim(),
        models: [],
        category: "custom",
      });
      setSuccess("自定义供应商已保存。");
      setCustom({ name: "", baseUrl: "", apiKey: "" });
      setCustomTest(null);
      await refresh();
    } catch (e: unknown) {
      setError(formatError(e));
    } finally {
      setBusyName(null);
    }
  }, [custom, canSaveCustom, refresh]);

  // ── saved-provider actions ──────────────────────────────────────────────
  const removeSaved = useCallback(
    async (name: string) => {
      setBusyName(name);
      setError(null);
      try {
        await deleteSavedProvider(name);
        await refresh();
        setSuccess("供应商已移除。");
      } catch (e: unknown) {
        setError(formatError(e));
      } finally {
        setBusyName(null);
      }
    },
    [refresh],
  );

  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-4 p-6 lg:grid-cols-4">
        <div className="space-y-3 lg:col-span-1">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
        <div className="grid grid-cols-2 gap-3 lg:col-span-3 lg:grid-cols-3">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-xl font-bold tracking-tight text-foreground-intense">
          LLM 配置
        </h1>
        <p className="mt-1 text-sm text-foreground-muted">
          设置全局默认模型，或按业务环节单独指定模型。
        </p>
      </div>

      {error && (
        <Alert
          variant="error"
          className="mb-4"
          dismissible
          onOpenChange={(o) => !o && setError(null)}
        >
          {error}
        </Alert>
      )}
      {success && (
        <Alert
          variant="success"
          className="mb-4"
          dismissible
          onOpenChange={(o) => !o && setSuccess(null)}
        >
          {success}
        </Alert>
      )}

      <div className="mb-5 rounded-lg border border-border bg-background-muted px-4 py-2.5 text-xs text-foreground-muted">
        优先级规则：环节单独设置 &gt; 全局默认模型 · 未单独设置的环节自动使用默认模型
      </div>

      {/* ── connected providers summary — includes custom providers, which
          otherwise have no visible surface after saving (their tab only
          holds the add form) ─────────────────────────────────────────── */}
      {saved.length > 0 && (
        <div className="mb-5 rounded-lg border border-border bg-card px-4 py-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-foreground-subtle">
              已配置供应商 · {saved.length}
            </span>
            <span className="text-[11px] text-foreground-subtle">
              密钥仅尾号显示
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {saved.map((s) => (
              <span
                key={s.name}
                className="flex items-center gap-2 rounded-sm border border-border bg-background-muted py-1 pl-3 pr-1.5 text-xs"
              >
                <span className="font-medium text-foreground">{s.label}</span>
                <span className="text-foreground-subtle">
                  sk-…{s.api_key_tail}
                  {s.models.length > 0 && ` · ${s.models.length} 模型`}
                </span>
                <button
                  type="button"
                  disabled={busyName === s.name}
                  onClick={() => void removeSaved(s.name)}
                  title={`移除 ${s.label}`}
                  className="flex h-5 w-5 items-center justify-center rounded-full text-foreground-subtle transition-colors hover:bg-background-strong hover:text-foreground disabled:opacity-40"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        {/* ── left rail: default model + agent config ────────────────── */}
        <aside className="lg:col-span-1">
          <Card frame={false} className="flex h-full flex-col gap-3 bg-card p-4">
            {/* global default */}
            <button
              className="rounded-lg border border-accent/40 bg-background-muted p-3 text-left transition-colors hover:bg-background-muted"
              onClick={() => openPicker(DEFAULT_KEY)}
            >
              <div className="mb-1 flex items-center gap-2">
                <span className="text-xs font-semibold text-foreground">
                  全局默认模型
                </span>
                <Badge variant="outline" className="text-[10px] text-accent">
                  兜底
                </Badge>
                <span className="ml-auto text-[11px] text-foreground-subtle">
                  点击配置
                </span>
              </div>
              <p className="text-sm font-medium text-foreground">{defaultLabel}</p>
              <p className="mt-0.5 text-[11px] text-foreground-subtle">
                所有环节未单独设置时使用此模型
              </p>
            </button>

            <Separator />

            {/* agent rows */}
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-foreground">
                环节模型配置
              </span>
            </div>

            <div className="flex flex-col gap-2">
              {AGENT_ORDER.map((key) => {
                const eff = effectiveModel(key);
                const overridden = modelCfg.agents[key]?.provider != null;
                return (
                  <button
                    key={key}
                    className="flex w-full items-center gap-2.5 rounded-lg border border-border bg-background-muted p-3 text-left transition-colors hover:bg-background-muted"
                    onClick={() => openPicker(key)}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-foreground">
                        {AGENT_LABELS[key]}
                      </span>
                      <span className="block truncate text-[11px] text-foreground-subtle">
                        {overridden
                          ? `${providerLabel(eff.provider ?? "")} ${eff.model}`
                          : `跟随默认 · ${defaultLabel}`}
                      </span>
                    </span>
                    {overridden ? (
                      <Badge variant="outline" className="shrink-0 text-[10px] text-foreground-info">
                        已单独设置
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="shrink-0 text-[10px] text-foreground-subtle">
                        继承默认
                      </Badge>
                    )}
                  </button>
                );
              })}
            </div>
          </Card>
        </aside>

        {/* ── right panel: provider catalog ──────────────────────────── */}
        <section className="lg:col-span-3">
          <Tabs defaultValue="preset">
            <TabsList className="mb-4">
              <TabsTrigger value="preset">预设供应商</TabsTrigger>
              <TabsTrigger value="custom">自定义供应商</TabsTrigger>
            </TabsList>

            <TabsContent value="preset">
              <div className="mb-4 flex items-center gap-3">
                <div className="relative max-w-sm flex-1">
                  <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-foreground-subtle">
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
                      <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5" />
                      <path d="M11 11l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                    </svg>
                  </span>
                  <Input
                    className="h-9 pl-9 text-sm"
                    placeholder="搜索预设供应商，如 DeepSeek、硅基流动…"
                    value={presetQuery}
                    onChange={(e) => setPresetQuery(e.target.value)}
                  />
                </div>
                <span className="text-xs text-foreground-subtle">
                  {filteredProviders.length} / {providers.length} 家
                </span>
              </div>

              {filteredProviders.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border-secondary p-10 text-center">
                  <p className="text-sm text-foreground-muted">
                    未找到与「{presetQuery.trim()}」匹配的预设供应商
                  </p>
                  <p className="mt-1 text-xs text-foreground-subtle">
                    试试切换到自定义供应商，或更换关键词
                  </p>
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {filteredProviders.map((p) => {
                    const existing = savedMap.get(p.name);
                    return (
                      <Card
                        key={p.name}
                        frame={false}
                        className={
                          existing
                            ? "border border-accent/40 bg-card p-4"
                            : "border border-border bg-card p-4"
                        }
                      >
                        <div className="mb-2 flex items-center justify-between">
                          <span className="flex items-center gap-2">
                            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-background-muted text-[10px] font-bold text-foreground-muted">
                              {initials(p.label)}
                            </span>
                            <span className="text-sm font-medium text-foreground">
                              {p.label}
                            </span>
                          </span>
                          {existing ? (
                            <Badge variant="outline" className="text-[10px] text-accent">
                              已连接
                            </Badge>
                          ) : (
                            <Badge variant="outline" className="text-[10px] text-foreground-subtle">
                              未连接
                            </Badge>
                          )}
                        </div>

                        <p className="mb-3 truncate text-[11px] text-foreground-subtle" title={p.url ?? ""}>
                          {p.url ?? "—"}
                        </p>

                        {existing ? (
                          <p className="mb-2 text-[11px] text-foreground-muted">
                            密钥 sk-…{existing.api_key_tail}
                            {existing.models.length > 0 &&
                              ` · ${existing.models.length} 个模型`}
                          </p>
                        ) : (
                          <Input
                            type="password"
                            className="mb-2 h-9 text-xs"
                            placeholder={`输入 ${p.label} 密钥`}
                            value={keys[p.name] ?? ""}
                            onChange={(e) =>
                              setKeys((prev) => ({
                                ...prev,
                                [p.name]: e.target.value,
                              }))
                            }
                          />
                        )}

                        {existing ? (
                          <Button
                            size="sm"
                            variant="outline"
                            className="w-full text-[11px]"
                            disabled={busyName === p.name}
                            onClick={() => void removeSaved(p.name)}
                          >
                            移除
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            className="w-full text-[11px]"
                            disabled={busyName === p.name}
                            onClick={() => void connectPreset(p)}
                          >
                            {busyName === p.name ? (
                              <Spinner className="h-3.5 w-3.5" />
                            ) : (
                              "连接并拉取模型"
                            )}
                          </Button>
                        )}
                      </Card>
                    );
                  })}
                </div>
              )}
            </TabsContent>

            <TabsContent value="custom">
              <p className="mb-4 text-xs text-foreground-muted">
                接入任意 OpenAI 兼容端点，适配中转站与自部署网关。
              </p>
              <Card frame={false} className="max-w-xl space-y-4 bg-card p-5">
                <Field>
                  <FieldLabel>服务商名称</FieldLabel>
                  <Input
                    value={custom.name}
                    onChange={(e) =>
                      setCustom((prev) => ({ ...prev, name: e.target.value }))
                    }
                    placeholder="如 我的中转站"
                  />
                </Field>

                <Field>
                  <FieldLabel>OpenAI 兼容 URL</FieldLabel>
                  <FieldDescription>
                    完整端点，例如 https://your-gateway.example/v1
                  </FieldDescription>
                  <Input
                    value={custom.baseUrl}
                    onChange={(e) =>
                      setCustom((prev) => ({ ...prev, baseUrl: e.target.value }))
                    }
                    placeholder="https://your-gateway.example/v1"
                  />
                </Field>

                <Field>
                  <FieldLabel>API 密钥</FieldLabel>
                  <Input
                    type="password"
                    value={custom.apiKey}
                    onChange={(e) =>
                      setCustom((prev) => ({ ...prev, apiKey: e.target.value }))
                    }
                    placeholder="sk-…"
                    autoComplete="off"
                  />
                </Field>

                {customTest && (
                  <Alert variant="success" layout="inline">
                    连接成功，端点可访问。
                  </Alert>
                )}

                <div className="flex gap-3 pt-1">
                  <Button
                    variant="outline"
                    disabled={busyName === "__custom__"}
                    onClick={() => void testCustom()}
                  >
                    {busyName === "__custom__" ? (
                      <Spinner className="h-4 w-4" />
                    ) : (
                      "测试连接"
                    )}
                  </Button>
                  <Button
                    disabled={!canSaveCustom || busyName === "__custom__"}
                    onClick={() => void saveCustom()}
                  >
                    保存供应商
                  </Button>
                </div>
              </Card>
            </TabsContent>
          </Tabs>
        </section>
      </div>

      {/* ── Amazon SP-API credentials (listing upload channel) ───────────── */}
      <SpapiSection onNotify={(msg) => setSuccess(msg)} onError={(msg) => setError(msg)} />

      {/* ── model picker dialog ──────────────────────────────────────── */}
      <Dialog open={pickerOpen} onOpenChange={setPickerOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{pickerTitle}</DialogTitle>
            <DialogDescription>
              {pickerAgent === DEFAULT_KEY
                ? "选择全局默认模型，所有未单独设置的环节将使用它。"
                : "优先使用环节单独设置，未设置时回退到全局默认模型。"}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 py-1">
            {/* inherit option — only for agent rows */}
            {pickerAgent !== DEFAULT_KEY && (
              <button
                className={`flex w-full items-center justify-between rounded-lg border p-3 text-left transition-colors ${
                  pickerChoice === "inherit"
                    ? "border-accent bg-primary-subtle"
                    : "border-border bg-background-muted hover:bg-background-muted"
                }`}
                onClick={() => setPickerChoice("inherit")}
              >
                <span className="text-sm font-medium text-foreground">
                  继承默认模型
                </span>
                <span className="text-xs text-foreground-subtle">
                  {defaultLabel}
                </span>
              </button>
            )}

            {/* grouped models */}
            {optionsByProvider.length === 0 && (
              <p className="text-xs text-foreground-subtle">
                暂无可用模型。请先在右侧连接供应商并拉取模型清单。
              </p>
            )}
            {optionsByProvider.map(({ provider, models }) => (
              <div key={provider.name}>
                <p className="mb-1.5 text-[11px] font-medium text-foreground-subtle">
                  {provider.label} · 已连接
                </p>
                <div className="space-y-1.5">
                  {models.map((m) => {
                    const val = `${provider.name}:${m.id}`;
                    const selected = pickerChoice === val;
                    return (
                      <button
                        key={m.id}
                        className={`flex w-full items-center justify-between rounded-lg border p-3 text-left transition-colors ${
                          selected
                            ? "border-accent bg-primary-subtle"
                            : "border-border bg-background-muted hover:bg-background-muted"
                        }`}
                        onClick={() => setPickerChoice(val)}
                      >
                        <span className="text-sm text-foreground">{m.name}</span>
                        {selected && (
                          <Badge variant="outline" className="text-[10px] text-accent">
                            已选
                          </Badge>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setPickerOpen(false)}>
              取消
            </Button>
            <Button
              disabled={busyName === "__model__"}
              onClick={() => void confirmPicker()}
            >
              {busyName === "__model__" ? (
                <Spinner className="h-4 w-4" />
              ) : (
                "确定"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function providerLabel(name: string): string {
  const map: Record<string, string> = {
    openai: "OpenAI",
    anthropic: "Anthropic",
    deepseek: "DeepSeek",
    google: "Google",
    minimax_cn: "MiniMax CN",
    groq: "Groq",
    siliconflow: "硅基流动",
    moonshotai_cn: "Moonshot CN",
  };
  return map[name] ?? name;
}

function initials(label: string): string {
  const parts = label.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

/** Amazon SP-API 凭据分区 — 凭据齐全时 Listing 卡的「上传 Amazon」按钮激活。 */
function SpapiSection({
  onNotify,
  onError,
}: {
  onNotify: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const [cfg, setCfg] = useState({
    client_id: "",
    client_secret: "",
    refresh_token: "",
    seller_id: "",
    marketplace_ids: "ATVPDKIKX0DER",
    region: "NA",
  });
  const [channel, setChannel] = useState<"api" | "export">("export");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void fetchSpapiConfig().then((saved) => {
      if (saved.client_id) {
        setCfg((prev) => ({
          ...prev,
          ...saved,
          marketplace_ids: (saved.marketplace_ids ?? ["ATVPDKIKX0DER"]).join(","),
          region: saved.region ?? "NA",
        }));
      }
      void fetchListingChannel().then(setChannel);
    });
  }, []);

  const input =
    "h-9 w-full rounded-lg border border-border bg-background-muted px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-accent/40";

  const save = async () => {
    setBusy(true);
    try {
      await saveSpapiConfig({
        client_id: cfg.client_id,
        client_secret: cfg.client_secret,
        refresh_token: cfg.refresh_token,
        seller_id: cfg.seller_id,
        marketplace_ids: cfg.marketplace_ids
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        region: cfg.region,
      });
      void fetchListingChannel().then(setChannel);
      onNotify("Amazon SP-API 凭据已保存。");
    } catch (e) {
      onError(formatError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mb-8 mt-6">
      <h2 className="mb-1 text-lg font-bold tracking-tight text-foreground-intense">
        Amazon 上传通道
      </h2>
      <p className="mb-4 text-sm text-foreground-muted">
        凭据齐全时 Listing 卡出现「上传 Amazon」按钮（自动上传）；未配置则退化为导出标准文件。
      </p>
      <Card frame={false} className="w-full space-y-4 bg-card p-5">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Field>
            <FieldLabel>Client ID</FieldLabel>
            <input
              className={input}
              value={cfg.client_id}
              onChange={(e) => setCfg({ ...cfg, client_id: e.target.value })}
            />
          </Field>
          <Field>
            <FieldLabel>Client Secret</FieldLabel>
            <input
              className={input}
              type="password"
              value={cfg.client_secret}
              onChange={(e) => setCfg({ ...cfg, client_secret: e.target.value })}
            />
          </Field>
          <Field>
            <FieldLabel>Refresh Token</FieldLabel>
            <input
              className={input}
              type="password"
              value={cfg.refresh_token}
              onChange={(e) => setCfg({ ...cfg, refresh_token: e.target.value })}
            />
          </Field>
          <Field>
            <FieldLabel>Seller ID</FieldLabel>
            <input
              className={input}
              value={cfg.seller_id}
              onChange={(e) => setCfg({ ...cfg, seller_id: e.target.value })}
            />
          </Field>
          <Field>
            <FieldLabel>Marketplace IDs（逗号分隔）</FieldLabel>
            <input
              className={input}
              value={cfg.marketplace_ids}
              onChange={(e) => setCfg({ ...cfg, marketplace_ids: e.target.value })}
            />
          </Field>
          <Field>
            <FieldLabel>Region</FieldLabel>
            <select
              className={input}
              value={cfg.region}
              onChange={(e) => setCfg({ ...cfg, region: e.target.value })}
            >
              <option value="NA">NA</option>
              <option value="EU">EU</option>
              <option value="FE">FE</option>
            </select>
          </Field>
        </div>
        <div className="flex items-center gap-3 pt-1">
          <Button disabled={busy} onClick={() => void save()}>
            {busy ? <Spinner className="h-4 w-4" /> : "保存凭据"}
          </Button>
          <span className="text-xs text-foreground-subtle">
            当前通道：{channel === "api" ? "API 自动上传" : "导出标准文件（退化）"}
            {cfg.client_secret.startsWith("••") ? " · 已存密钥掩码显示" : ""}
          </span>
        </div>
      </Card>
    </section>
  );
}

function formatError(e: unknown): string {
  if (e instanceof ConfigApiError) {
    const statusPart = e.status ? `HTTP ${e.status}：` : "";
    return `${statusPart}${e.message}`;
  }
  return e instanceof Error ? e.message : String(e);
}
