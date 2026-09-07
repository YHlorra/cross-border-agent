// Per-session chat store ( addendum — multi-session parallel).
// Pure helpers around a session's entry list. No React dependencies —
// workbench composes them into useState/useCallback.
//
// Storage shape on the workbench: `Record<sessionId, SessionEntry[]>`
// (flat arrays). Helpers operate on the array directly; `sessionBusy` is
// the cheap "is this list currently running" check.

import type { ListingRunState } from "@/components/chat/listing-run-state";
import type { ChatRunState } from "@/components/chat/chat-run-state";
import type { RunState } from "@/components/chat/run-state";

export type SessionEntry =
  | { id: number; kind: "user"; text: string }
  | { id: number; kind: "agent"; run: RunState }
  | { id: number; kind: "listing"; run: ListingRunState }
  | { id: number; kind: "chat"; run: ChatRunState }
  | { id: number; kind: "system"; text: string };

/** Whether a session's entry list currently has a live agent/listing/chat
 *  run. Cheap O(n) over the last entries; n is bounded by turns in a session. */
export function sessionBusy(entries: SessionEntry[] | undefined | null): boolean {
  if (!entries) return false;
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e.kind === "agent" || e.kind === "listing" || e.kind === "chat") {
      return e.run.phase === "running";
    }
  }
  return false;
}

/** IDs of sessions currently running (for sidebar indicator). */
export function runningSessionIds(
  entriesBySession: Readonly<Record<string, SessionEntry[]>>,
): Set<string> {
  const ids = new Set<string>();
  for (const sid in entriesBySession) {
    if (sessionBusy(entriesBySession[sid])) ids.add(sid);
  }
  return ids;
}

/** Append one entry to the list. Returns a new array reference so React
 *  state setters trigger re-render. */
export function appendEntry(
  entries: SessionEntry[],
  entry: SessionEntry,
): SessionEntry[] {
  return [...entries, entry];
}

/** Patch a run/agent|listing entry by its id (immutable replace). Returns
 *  the same array reference when the id isn't found so a stale stream callback
 *  no-ops cleanly instead of crashing the chat. */
export function patchEntryRun(
  entries: SessionEntry[],
  entryId: number,
  patch: (e: SessionEntry) => SessionEntry,
): SessionEntry[] {
  let found = false;
  const next = entries.map((e) => {
    if (e.id !== entryId) return e;
    found = true;
    return patch(e);
  });
  return found ? next : entries;
}

/** Mark a run entry's phase = "cancelled" (used on AbortError). No-op if the
 *  entry is no longer present (stale stream). */
export function markEntryCancelled(
  entries: SessionEntry[],
  entryId: number,
): SessionEntry[] {
  return patchEntryRun(entries, entryId, (e) => {
    if (e.kind !== "agent" && e.kind !== "listing" && e.kind !== "chat") return e;
    return { ...e, run: { ...e.run, phase: "cancelled" } } as SessionEntry;
  });
}