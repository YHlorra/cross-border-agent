/**
 * Shared types + error class for the LLM config page.
 * Kept separate from selection-api.ts because that file is a "use server"
 * module — only async functions may be exported from it.
 */

export interface ProviderOption {
  name: string;
  label: string;
  url?: string;
  category: "native" | "registry";
  can_list_models: boolean;
}

export interface ModelOption {
  id: string;
  name: string;
}

export interface SavedProvider {
  name: string;
  label: string;
  base_url: string | null;
  api_key_set: boolean;
  api_key_tail: string;
  models: ModelOption[];
  category: string;
}

export interface ProviderRecord {
  name: string;
  label?: string;
  base_url?: string | null;
  api_key: string;
  models?: ModelOption[];
  category?: string;
}

export interface ModelRef {
  provider: string | null;
  model: string | null;
  base_url?: string | null;
}

export interface ModelConfig {
  default: ModelRef;
  agents: Record<string, ModelRef | null>;
}

export const AGENT_LABELS: Record<string, string> = {
  selection: "选品",
  listing: "Listing 生成",
  monitor: "监控",
  store: "店铺管理",
};

export const AGENT_ORDER = ["selection", "listing", "monitor", "store"];

export interface ConfigSaveResult {
  ok: boolean;
  written_to: string;
  snapshot: ConfigSnapshot;
}

export interface ConfigSnapshot {
  primary_provider: string | null;
  primary_model: string | null;
  cheap_provider: string | null;
  cheap_model: string | null;
  base_url: string | null;
  api_key_set: boolean;
  /** Runtime-resolved selection model (model_config.json via the config
   * page) — the authoritative badge label; env vars alone routinely lag. */
  effective_selection?: { provider: string; model: string } | null;
}

export interface WireError {
  event: "error";
  code: string;
  message: string;
  status?: number | null;
  body?: unknown;
}

export class ConfigApiError extends Error {
  code: string;
  status: number | null;
  constructor(message: string, code: string, status: number | null) {
    super(message);
    this.name = "ConfigApiError";
    this.code = code;
    this.status = status;
  }
}
