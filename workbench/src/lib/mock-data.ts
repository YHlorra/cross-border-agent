/**
> * Static reference data used by the frontend (e.g. ScoreMeter axis labels).
> *
> * Runtime product/agent data now comes from the Python backend via
> * `lib/selection-api.ts`. Static product / history / marketSummary arrays
> * have been removed; the implementation is wired to real NDJSON streams.
> */

import type { DimensionKey } from "./selection-types";

export interface DimensionMeta {
  key: DimensionKey;
  label: string;
  weight: number;
}

export const dimensions: DimensionMeta[] = [
  { key: "market_demand", label: "市场需求", weight: 0.25 },
  { key: "competition", label: "竞争度", weight: 0.2 },
  { key: "profit", label: "利润空间", weight: 0.3 },
  { key: "seasonality", label: "季节性", weight: 0.1 },
  { key: "repurchase", label: "复购率", weight: 0.15 },
];

export const quickSuggestions = [
  { text: "无线耳机有什么好卖的？", icon: "🎧" },
  { text: "宠物用品选品分析", icon: "🐾" },
  { text: "户外露营装备，预算 1 万", icon: "⛺" },
  { text: "家居小工具，要轻小件", icon: "🏠" },
];