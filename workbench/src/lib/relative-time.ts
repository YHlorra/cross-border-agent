/**
 * Relative time formatting — coarse granularity:
 * now / 3m / 2h / 5d / 3w / 2mo / 1y. Pure function, zero dependency.
 * Input: epoch ms or ISO string. Future timestamps clamp to "now".
 */
export function relativeTime(input: number | string | null | undefined): string {
  if (input == null || input === "") return "";
  const ts = typeof input === "number" ? input : Date.parse(input);
  if (!Number.isFinite(ts)) return "";
  const diff = Math.max(0, Date.now() - ts);
  const min = 60_000;
  if (diff < min) return "now";
  const h = 60 * min;
  if (diff < h) return `${Math.floor(diff / min)}m`;
  const d = 24 * h;
  if (diff < d) return `${Math.floor(diff / h)}h`;
  if (diff < 7 * d) return `${Math.floor(diff / d)}d`;
  const w = 7 * d;
  if (diff < 30 * d) return `${Math.floor(diff / w)}w`;
  const mo = 30 * d;
  if (diff < 365 * d) return `${Math.floor(diff / mo)}mo`;
  return `${Math.floor(diff / (365 * d))}y`;
}
