// 偏好 RAG — 前端 CRUD wrapper。Preferences are long-term memory
// surfaced through the sidebar "偏好" entry ; the composer no
// longer carries transient chips. Note : run-time injection of
// these preferences into the agent loop is not wired yet — storage and
// retrieval are live, prompt injection is a pending change.

export interface Preference {
  id: number;
  category: string;
  preference_text: string;
  source_run_id: string;
  source_session_id: string;
  created_at: string;
  last_reinforced_at: string | null;
  reinforcement_count: number;
  expires_at: string | null;
}

export async function fetchPreferences(): Promise<Preference[]> {
  try {
    const res = await fetch("/api/preferences", { cache: "no-store" });
    if (!res.ok) return [];
    const data = (await res.json()) as Preference[];
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

export async function addPreference(
  text: string,
  category = "user-added",
): Promise<number | null> {
  try {
    const res = await fetch("/api/preferences", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, category }),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { id?: number };
    return data.id ?? null;
  } catch {
    return null;
  }
}

export async function deletePreference(id: number): Promise<boolean> {
  try {
    const res = await fetch(`/api/preferences/${id}`, { method: "DELETE" });
    return res.ok;
  } catch {
    return false;
  }
}

/** 编辑偏好——PATCH /api/preferences/{id} {text?, category?}。
 *  服务端在 text 变更时重 embed（pgvector / sqlite-vec）。 */
export async function updatePreference(
  id: number,
  patch: { text?: string; category?: string },
): Promise<boolean> {
  try {
    const res = await fetch(`/api/preferences/${id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(patch),
    });
    return res.ok;
  } catch {
    return false;
  }
}