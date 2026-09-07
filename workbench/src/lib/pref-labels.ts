/** Display labels for the canonical preference keys. Kept separate from the
 *  preference-alias table in `src/agent/nodes/intent.py` so the frontend
 *  never depends on Python module paths; the keys are the wire contract. */
export const PREF_LABELS: Record<string, string> = {
  light: "轻小件",
  repurchase: "高复购",
  low_price: "价格 < 50 元",
};

export function prefLabel(key: string): string {
  return PREF_LABELS[key] ?? key;
}