"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Sidebar } from "./sidebar";
import { ConfigView } from "./views/config-view";
import { ChatHeader } from "./chat/chat-header";
import { ChatHero } from "./chat/chat-hero";
import { Composer } from "./chat/composer";
import {
  AgentRunMessage,
  UserMessage,
} from "./chat/agent-run-message";
import { ChatRunMessage } from "./chat/chat-run-message";
import { ListingRunMessage } from "./chat/listing-run-message";
import { replaySessionEvents } from "./chat/run-state";
import {
  applyChatEvent,
  newChatRun,
  type ChatRunState,
} from "./chat/chat-run-state";
import {
  deleteSession,
  fetchConfig,
  fetchSessionEvents,
  fetchSessions,
  type ConfigSnapshot,
} from "@/lib/selection-api";
import { compactSession, runChat } from "@/lib/chat-api";
import type { ChatWireEvent } from "@/lib/chat-types";
import { parseCommand } from "@/lib/commands";
import { ListingListView } from "./views/listing-list-view";
import { PreferencesView } from "./views/preferences-view";
import {
  appendEntry,
  markEntryCancelled,
  patchEntryRun,
  runningSessionIds,
  sessionBusy,
  type SessionEntry,
} from "@/lib/session-store";
import type {
  ScoredCandidate,
  SessionListItem,
} from "@/lib/selection-types";

type View = "chat" | "config" | "listings" | "preferences";

export function Workbench() {
  const [view, setView] = useState<View>("chat");
  /** Per-session entry lists — sparse Map. Sidebar/Composer/ChatBody render
   *  from this; switching sessions is a focused-session change, not a data
   *  swap. Other sessions' runs continue to write here even while hidden. */
  const [entriesBySession, setEntriesBySession] = useState<
    Record<string, SessionEntry[]>
  >({});
  const [query, setQuery] = useState("");
  // 偏好已迁移到「偏好」视图，composer 只留文本输入；
  // 偏好注入运行链路尚未接线（ 遗留），信号可写进 query 文本。

  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [configSnapshot, setConfigSnapshot] = useState<ConfigSnapshot | null>(
    null,
  );
  /** session id of the currently focused timeline. */
  const [focusedSessionId, setFocusedSessionId] = useState<string | null>(null);

  // Stable id source for entry rows. Persists across re-renders.
  const idRef = useRef(0);
  // Per-session current AbortController (only set while a run is in flight).
  // Clearing happens in finally; do not read while another run replaces it.
  const cancelBySessionRef = useRef<Record<string, AbortController | null>>(
    {},
  );
  // Dedupe in-flight openSession fetches (/G3) and track the latest pending
  // session so a quick double-click doesn't race the timeline.
  const openSessionRequestRef = useRef(0);
  const pendingSessionIdRef = useRef<string | null>(null);

  // Stick-to-bottom scrolling for the message flow.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);

  const focusedEntries = useMemo<SessionEntry[]>(
    () => (focusedSessionId ? entriesBySession[focusedSessionId] ?? [] : []),
    [entriesBySession, focusedSessionId],
  );
  const focusedBusy = useMemo(
    () =>
      sessionBusy(
        focusedSessionId ? entriesBySession[focusedSessionId] : undefined,
      ),
    [entriesBySession, focusedSessionId],
  );
  /** Globally-derived set of session ids currently running — feeds the
   *  sidebar running indicator. */
  const activeRunningSessionIds = useMemo(
    () => runningSessionIds(entriesBySession),
    [entriesBySession],
  );

  // ── ui-refresh drawer / draft snapshot / queue / artifact ──
  const [drawerOpen, setDrawerOpen] = useState(false);
  const closeDrawer = useCallback(() => {
    setDrawerOpen(false);
    document.getElementById("sidebar-toggle")?.focus();
  }, []);

  // Draft snapshot: per-session unsent text survives session switches
  // (codex ComposerDraftSnapshot). Stash on focus-out, restore on focus-in.
  const draftsRef = useRef<Record<string, string>>({});
  const queryRef = useRef(query);
  queryRef.current = query;
  const prevFocusRef = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevFocusRef.current;
    prevFocusRef.current = focusedSessionId;
    // null = the empty-state "new task" surface — its draft lives under "".
    if (prev !== focusedSessionId) {
      const prevKey = prev ?? "__new__";
      draftsRef.current[prevKey] = queryRef.current;
      setQuery(draftsRef.current[focusedSessionId ?? "__new__"] ?? "");
    }
  }, [focusedSessionId]);

  // Message queue: submits during a live run park per-session and auto-send
  // on that session's live running→final transition. Replay hydration never
  // sets liveRunSessionRef, so restoring history can't fire the queue.
  const [queuedBySession, setQueuedBySession] = useState<
    Record<string, string[]>
  >({});
  const liveRunSessionRef = useRef<string | null>(null);
  const focusedQueue = focusedSessionId
    ? queuedBySession[focusedSessionId] ?? []
    : [];

  // Escape closes the mobile drawer.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeDrawer();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [drawerOpen, closeDrawer]);

  /** Latest settled listing run in the focused session — artifact panel. */
  const artifact = useMemo(() => {
    for (let i = focusedEntries.length - 1; i >= 0; i--) {
      const e = focusedEntries[i];
      if (e.kind === "listing") {
        return e.run.phase === "final" && !e.run.hardFailed ? e.run : null;
      }
    }
    return null;
  }, [focusedEntries]);
  const [artifactOpen, setArtifactOpen] = useState(false);
  /** ui-refresh per-session one-line progress (last resolved tool
   *  call) for the sidebar metadata row. */
  const sessionSummaries = useMemo(() => {
    const out: Record<string, string> = {};
    for (const [sid, entries] of Object.entries(entriesBySession)) {
      for (let i = entries.length - 1; i >= 0; i--) {
        const e = entries[i];
        if (e.kind !== "agent" && e.kind !== "chat") continue;
        // Summary lives on loop-run TurnToolCall only; legacy graph events
        // (ToolCallEvent) carry no tool_result payload.
        const calls = e.kind === "chat"
          ? e.run.turns.flatMap((t) => t.toolCalls)
          : e.run.agentSkill
            ? e.run.turns.flatMap((t) => t.toolCalls)
            : [];
        for (let j = calls.length - 1; j >= 0; j--) {
          const c = calls[j];
          if (c.summary) {
            out[sid] = `${c.tool} ${c.summary}`.slice(0, 40);
            break;
          }
        }
        break;
      }
    }
    return out;
  }, [entriesBySession]);

  const refreshSessions = useCallback(async () => {
    const items = await fetchSessions();
    setSessions(items);
  }, []);

  useEffect(() => {
    void refreshSessions();
  }, [refreshSessions]);

  useEffect(() => {
    void fetchConfig().then(setConfigSnapshot);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) {
      el.scrollTo({ top: el.scrollHeight });
    }
  }, [focusedEntries]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }, []);

  /** Push a system notice into the focused session. If no session is focused
   *  yet (e.g. user opens with an empty hero and triggers a routed reject),
   *  create an ephemeral "draft" session id so the message isn't lost. */
  const pushSystem = useCallback((text: string) => {
    const id = ++idRef.current;
    setEntriesBySession((prev) => {
      const sid =
        focusedSessionIdRef.current ??
        Object.keys(prev).at(-1) ??
        (() => {
          // Open ephemeral session so the message lives somewhere visible.
          const newSid = crypto.randomUUID();
          pendingSessionIdRef.current = null;
          setFocusedSessionId(newSid);
          focusedSessionIdRef.current = newSid;
          return newSid;
        })();
      const existing = prev[sid] ?? [];
      return { ...prev, [sid]: [...existing, { id, kind: "system", text }] };
    });
    stickRef.current = true;
  }, []);
  // Mirror focusedSessionId into a ref so pushSystem's setter can read the
  // latest value without re-creating the callback every render.
  const focusedSessionIdRef = useRef<string | null>(null);
  focusedSessionIdRef.current = focusedSessionId;

  /** general-agent-chat — plain text goes to the general agent loop (/chat).
   *  Same per-session lifecycle contract as submit: the selection pipeline
   *  is no longer the default path for un-routed input. */
  const submitChat = useCallback(
    async (q: string) => {
      const trimmed = q.trim();
      if (!trimmed) return;
      const sessionId =
        focusedSessionIdRef.current ?? crypto.randomUUID();
      if (!focusedSessionIdRef.current) setFocusedSessionId(sessionId);
      liveRunSessionRef.current = sessionId;
      setQuery("");
      const userId = ++idRef.current;
      const runId = ++idRef.current;
      const userEntry: SessionEntry = {
        id: userId,
        kind: "user",
        text: trimmed,
      };
      const runEntry: SessionEntry = {
        id: runId,
        kind: "chat",
        run: newChatRun(trimmed),
      };
      setEntriesBySession((prev) => {
        const existing = prev[sessionId] ?? [];
        return { ...prev, [sessionId]: [...existing, userEntry, runEntry] };
      });
      stickRef.current = true;

      const controller = new AbortController();
      cancelBySessionRef.current[sessionId] = controller;

      const onEvent = (event: ChatWireEvent) => {
        setEntriesBySession((prev) => {
          const s = prev[sessionId];
          if (!s) return prev;
          const next = patchEntryRun(s, runId, (e) => {
            if (e.kind !== "chat") return e;
            return { ...e, run: applyChatEvent(e.run, event) };
          });
          return next === s ? prev : { ...prev, [sessionId]: next };
        });
      };

      try {
        await runChat(trimmed, { sessionId }, onEvent, controller.signal);
        void refreshSessions();
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          setEntriesBySession((prev) => {
            const s = prev[sessionId];
            if (!s) return prev;
            return {
              ...prev,
              [sessionId]: markEntryCancelled(s, runId),
            };
          });
          return;
        }
        const errorEvent: ChatWireEvent = {
          event: "error",
          code: "ClientError",
          message: err instanceof Error ? err.message : String(err),
        };
        onEvent(errorEvent);
      } finally {
        if (cancelBySessionRef.current[sessionId] === controller) {
          cancelBySessionRef.current[sessionId] = null;
        }
      }
    },
    [refreshSessions],
  );

  /**  —「转 Listing 生成」不再直连流水线：发一条聊天消息，由通用
   *  agent 在注入的 Listing 方法论指引下调 run_listing / save_listing CLI。 */
  const askForListing = useCallback(
    (candidate: ScoredCandidate) => {
      const label = candidate.name_en || candidate.name_cn;
      void submitChat(`为「${label}」生成 Amazon Listing 草稿`);
    },
    [submitChat],
  );

  /**  — local 命令 handler（ Addendum 5 解耦边界）：加 local
   *  命令 = COMMANDS 加一条 + 这里的 switch 加一条。永远不进 /chat。 */
  const runLocalCommand = useCallback(
    (commandId: string) => {
      if (commandId === "new") {
        // 切到 "new task" 空态（workbench 把 focusedSessionId=null 当一等公民，
        // 草稿快照有 "__new__" 键——line 118 注释）；运行中的会话继续后台跑。
        setQuery("");
        setFocusedSessionId(null);
        return;
      }
      if (commandId === "compact") {
        const sid = focusedSessionIdRef.current;
        if (!sid) {
          pushSystem("当前没有会话可压缩。");
          return;
        }
        pushSystem("正在压缩会话…");
        void compactSession(sid).then((out) => {
          if (out.ok) {
            pushSystem(out.detail ? `会话已压缩：${out.detail}` : "会话已压缩。");
          } else {
            pushSystem(`压缩失败：${out.detail}`);
          }
        });
        return;
      }
      pushSystem(`未知本地命令：/${commandId}`);
    },
    [pushSystem],
  );

  /**  Addendum  — single-conversation router, NO hard
   *  routes left. Slash text and plain text take the SAME path: /chat, where
   *  the server injects the matching domain skill as a system-prompt appendix
   *  and the agent flexibly drives the CLI tools (run_selection / run_listing
   *  / save_listing). parseCommand now feeds the composer palette and the
   *  /选品 usage guard only. Per-session busy — other sessions running does
   *  NOT block submission in the focused session. */
  const submitRouted = useCallback(
    (text: string) => {
      if (!text.trim()) return; // empty Enter while running must not enqueue
      const parsed = parseCommand(text);
      //  解耦（ Addendum 5）：local 命令在前端派发，
      // 不进 /chat；busy 时仍允许（/new 切换不打断；/compact 走自己节奏）。
      if (parsed && parsed.command.kind === "local") {
        setQuery("");
        runLocalCommand(parsed.command.id);
        return;
      }
      if (parsed && parsed.command.id === "selection" && !parsed.rest) {
        pushSystem("用法：/选品 <需求描述>，例如 /选品 无线耳机 预算 3000");
        return;
      }
      if (focusedBusy) {
        // ui-refresh park the message instead of rejecting it.
        const sid = focusedSessionId;
        if (sid) {
          setQueuedBySession((prev) => ({
            ...prev,
            [sid]: [...(prev[sid] ?? []), text.trim()],
          }));
          setQuery("");
        } else {
          pushSystem("当前会话正在运行，请稍候或先停止后再试。");
        }
        return;
      }
      liveRunSessionRef.current = focusedSessionId;
      void submitChat(text);
    },
    [focusedBusy, focusedSessionId, submitChat, pushSystem, runLocalCommand],
  );

  /** Live running→final transition releases the next queued message;
   *  cancelled clears the queue (stop = user intent); error keeps it. */
  const prevBusyRef = useRef(false);
  useEffect(() => {
    const was = prevBusyRef.current;
    prevBusyRef.current = focusedBusy;
    if (!(was && !focusedBusy)) return;
    const sid = focusedSessionId;
    if (!sid || liveRunSessionRef.current !== sid) return;
    liveRunSessionRef.current = null;
    const entries = entriesBySession[sid];
    let phase = "";
    if (entries) {
      for (let i = entries.length - 1; i >= 0; i--) {
        const e = entries[i];
        if (e.kind === "agent" || e.kind === "listing" || e.kind === "chat") {
          phase = e.run.phase;
          break;
        }
      }
    }
    const q = queuedBySession[sid] ?? [];
    if (q.length === 0) return;
    if (phase === "final") {
      setQueuedBySession((prev) => ({ ...prev, [sid]: q.slice(1) }));
      const next = q[0];
      // Submit synchronously (not via setTimeout): a fire-and-forget timer is
      // throttled in hidden/background pages (observed 4-5s clamps in headless
      // runs), which starves the queued message and breaks the dequeue
      // contract. Re-entrancy is safe — the effect re-run sees q empty and
      // returns; there is no cleanup that could cancel anything.
      void submitRouted(next);
    }
    if (phase === "cancelled") {
      setQueuedBySession((prev) => ({ ...prev, [sid]: [] }));
    }
  }, [focusedBusy, focusedSessionId, entriesBySession, queuedBySession, submitRouted]);

  const removeQueued = useCallback(
    (index: number) => {
      const sid = focusedSessionId;
      if (!sid) return;
      setQueuedBySession((prev) => ({
        ...prev,
        [sid]: (prev[sid] ?? []).filter((_, i) => i !== index),
      }));
    },
    [focusedSessionId],
  );

  const handleConfigSaved = useCallback(
    (snapshot: ConfigSnapshot) => {
      setConfigSnapshot(snapshot);
      void refreshSessions();
    },
    [refreshSessions],
  );

  const newSession = useCallback(() => {
    openSessionRequestRef.current += 1;
    pendingSessionIdRef.current = null;
    setFocusedSessionId(null);
    stickRef.current = true;
    setView("chat");
  }, []);

  /** open a session in the timeline (no LLM re-run). Per-session
   *  parallel: switching sessions no longer aborts the focused session's run;
   *  other sessions keep going. Repeated click on the same session is a no-op
   *  via ``pendingSessionIdRef``. */
  const openSession = useCallback(
    async (sessionId: string) => {
      if (pendingSessionIdRef.current === sessionId) return;

      const requestId = ++openSessionRequestRef.current;
      pendingSessionIdRef.current = sessionId;
      const { events, error } = await fetchSessionEvents(sessionId);
      if (
        requestId !== openSessionRequestRef.current ||
        pendingSessionIdRef.current !== sessionId
      ) {
        return;
      }
      pendingSessionIdRef.current = null;
      if (error) {
        const sysId = ++idRef.current;
        const sysEntry: SessionEntry = {
          id: sysId,
          kind: "system",
          text: `打开会话失败：${error}`,
        };
        setEntriesBySession((prev) => ({
          ...prev,
          [sessionId]: [...(prev[sessionId] ?? []), sysEntry],
        }));
        setFocusedSessionId(sessionId);
        stickRef.current = true;
        return;
      }
      if (events.length === 0) {
        const sysId = ++idRef.current;
        const sysEntry: SessionEntry = {
          id: sysId,
          kind: "system",
          text: "该会话无事件",
        };
        setEntriesBySession((prev) => ({
          ...prev,
          [sessionId]: [...(prev[sessionId] ?? []), sysEntry],
        }));
        setFocusedSessionId(sessionId);
        stickRef.current = true;
        return;
      }

      const replays = replaySessionEvents(events);
      // 历史 user_message 携带 budget_cny（回放契约）——偏好/预算已迁移到
      // 「偏好」视图作为长期默认，openSession 不再回填 composer 临时态。

      const newEntries: SessionEntry[] = [];
      for (const r of replays) {
        const userId = ++idRef.current;
        const runRowId = ++idRef.current;
        newEntries.push({ id: userId, kind: "user", text: r.query });
        if (r.kind === "listing") {
          newEntries.push({ id: runRowId, kind: "listing", run: r.listingRun });
        } else if (r.kind === "chat") {
          newEntries.push({ id: runRowId, kind: "chat", run: r.chatRun });
        } else {
          newEntries.push({ id: runRowId, kind: "agent", run: r.run });
        }
      }
      setEntriesBySession((prev) => ({ ...prev, [sessionId]: newEntries }));
      stickRef.current = true;
      setFocusedSessionId(sessionId);
      setView("chat");
    },
    [],
  );

  /** Abort the focused session's in-flight run (Composer cancel button). */
  const cancelFocused = useCallback(() => {
    const sid = focusedSessionIdRef.current;
    if (!sid) return;
    cancelBySessionRef.current[sid]?.abort();
  }, []);

  /** Abort any in-flight run for a specific session id (sidebar stop button).
   *  Used to cancel runs in non-focused sessions. */
  const stopSession = useCallback((sessionId: string) => {
    cancelBySessionRef.current[sessionId]?.abort();
  }, []);

  const clearAllSessions = useCallback(async () => {
    openSessionRequestRef.current += 1;
    pendingSessionIdRef.current = null;
    // Abort every in-flight run across all sessions; per-session parallel
    // means we must touch the whole map, not just the focused one.
    for (const sid in cancelBySessionRef.current) {
      cancelBySessionRef.current[sid]?.abort();
    }
    cancelBySessionRef.current = {};
    await Promise.all(sessions.map((s) => deleteSession(s.session_id)));
    setSessions([]);
    setEntriesBySession({});
    setFocusedSessionId(null);
    stickRef.current = true;
    void refreshSessions();
  }, [sessions, refreshSessions]);

  const chatBody = (
    <div
      ref={(el) => {
        scrollRef.current = el;
      }}
      onScroll={handleScroll}
      className="h-full overflow-y-auto"
    >
      {focusedEntries.length === 0 ? (
        <ChatHero onPick={(text) => submitRouted(text)} />
      ) : (
        <div className="mx-auto max-w-4xl space-y-5 px-6 py-8 xl:max-w-5xl">
          {focusedEntries.map((e) => {
            if (e.kind === "user") return <UserMessage key={e.id} text={e.text} />;
            if (e.kind === "system") {
              return (
                <div
                  key={e.id}
                  className="text-center text-sm text-fg-muted italic"
                  role="status"
                >
                  {e.text}
                </div>
              );
            }
            if (e.kind === "listing")
              return (
                <ListingRunMessage
                  key={e.id}
                  run={e.run}
                  onGoConfig={() => setView("config")}
                />
              );
            if (e.kind === "chat")
              return (
                <ChatRunMessage
                  key={e.id}
                  run={e.run}
                  onGoConfig={() => setView("config")}
                  onGoListing={askForListing}
                />
              );
            return (
              <AgentRunMessage
                key={e.id}
                run={e.run}
                onRetry={() => void submitChat(e.run.query)}
                onGoConfig={() => setView("config")}
                onGoListing={askForListing}
                onAsk={setQuery}
              />
            );
          })}
        </div>
      )}
    </div>
  );

  const sidebarProps = {
    sessions,
    runningSessionIds: activeRunningSessionIds,
    activeView: view,
    activeSessionId: focusedSessionId,
    sessionSummaries,
    onRefreshSessions: refreshSessions,
    onClearAllSessions: clearAllSessions,
    configSnapshot,
    onOpenConfig: () => setView((v) => (v === "config" ? "chat" : "config")),
    onOpenSession: openSession,
    onNewTask: newSession,
    onOpenListings: () => setView("listings"),
    onOpenPreferences: () => setView("preferences"),
    onStopSession: stopSession,
  };
  /** Drawer variant: navigate-and-close so the pane never blocks <md. */
  const drawerSidebarProps = {
    ...sidebarProps,
    onOpenConfig: () => {
      sidebarProps.onOpenConfig();
      closeDrawer();
    },
    onOpenSession: (id: string) => {
      void openSession(id);
      closeDrawer();
    },
    onNewTask: () => {
      newSession();
      closeDrawer();
    },
    onOpenListings: () => {
      setView("listings");
      closeDrawer();
    },
    onOpenPreferences: () => {
      setView("preferences");
      closeDrawer();
    },
  };

  return (
    <div className="flex h-dvh overflow-hidden bg-canvas">
      <div className="hidden h-dvh shrink-0 md:block">
        <Sidebar {...sidebarProps} />
      </div>

      {/* <md — overlay drawer */}
      {drawerOpen && (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-label="会话侧栏">
          <div
            className="absolute inset-0 bg-black/40"
            onClick={closeDrawer}
            aria-hidden
          />
          <div className="absolute inset-y-0 left-0 h-dvh">
            <Sidebar {...drawerSidebarProps} />
          </div>
        </div>
      )}

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden border-l border-border-muted">
        {view === "config" ? (
          <>
            <ChatHeader
              configSnapshot={configSnapshot}
              busy={focusedBusy}
              onNewSession={newSession}
              onOpenConfig={() => setView("chat")}
              onToggleSidebar={() => setDrawerOpen(true)}
            />
            <div className="flex-1 overflow-y-auto">
              <ConfigView snapshot={configSnapshot} onSaved={handleConfigSaved} />
            </div>
          </>
        ) : view === "listings" ? (
          <>
            <ChatHeader
              configSnapshot={configSnapshot}
              busy={focusedBusy}
              onNewSession={newSession}
              onOpenConfig={() => setView("chat")}
              onToggleSidebar={() => setDrawerOpen(true)}
            />
            <div className="flex-1 overflow-y-auto">
              <ListingListView />
            </div>
          </>
        ) : view === "preferences" ? (
          <>
            <ChatHeader
              configSnapshot={configSnapshot}
              busy={focusedBusy}
              onNewSession={newSession}
              onOpenConfig={() => setView("chat")}
              onToggleSidebar={() => setDrawerOpen(true)}
            />
            <div className="flex-1 overflow-y-auto">
              <PreferencesView onClearAllSessions={clearAllSessions} />
            </div>
          </>
        ) : (
          <div className="flex min-h-0 flex-1">
            <div className="flex min-w-0 flex-1 flex-col">
              <ChatHeader
                configSnapshot={configSnapshot}
                busy={focusedBusy}
                onNewSession={newSession}
                onOpenConfig={() => setView("chat")}
                onToggleSidebar={() => setDrawerOpen(true)}
                artifactAvailable={artifact !== null}
                artifactOpen={artifactOpen}
                onToggleArtifact={() => setArtifactOpen((o) => !o)}
              />
              <div className="relative min-h-0 flex-1">{chatBody}</div>
              <footer className="px-6 pb-5 pt-2">
                <div className="mx-auto max-w-4xl xl:max-w-5xl">
                  <Composer
                    query={query}
                    onQueryChange={setQuery}
                    running={focusedBusy}
                    onSubmit={() => submitRouted(query)}
                    onCancel={cancelFocused}
                  />
                  {focusedQueue.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {focusedQueue.map((q, i) => (
                        <span
                          key={String(i) + "-" + q.slice(0, 8)}
                          className="flex items-center gap-1.5 rounded-md bg-background-muted px-2.5 py-1 text-[11px] text-foreground-muted"
                        >
                          <span className="tabular-nums text-foreground-subtle">
                            排队 #{i + 1}
                          </span>
                          <span className="max-w-[200px] truncate">{q}</span>
                          <button
                            type="button"
                            onClick={() => removeQueued(i)}
                            title="撤回该消息"
                            className="text-foreground-subtle transition-colors hover:text-error"
                          >
                            ×
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                  <p className="mt-2 text-center text-[11px] text-foreground-subtle">
                    选品结果由 AI 生成，采购决策请结合供应商实地验证。
                  </p>
                </div>
              </footer>
            </div>

            {/* Artifact panel — ≥xl only; listing draft as a first-class
                product (Artifacts/Canvas pattern). */}
            {artifactOpen && artifact && (
              <aside className="hidden w-[420px] shrink-0 flex-col overflow-y-auto border-l border-border-muted bg-card xl:flex">
                <div className="sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-border-muted bg-card px-4 py-2.5">
                  <p className="truncate text-xs font-semibold text-foreground">
                    Listing 产物 · {artifact.candidate.name_cn}
                  </p>
                  <button
                    type="button"
                    onClick={() => setArtifactOpen(false)}
                    className="shrink-0 rounded-md px-1.5 py-0.5 text-xs text-foreground-subtle transition-colors hover:text-foreground"
                  >
                    收起
                  </button>
                </div>
                <div className="space-y-4 p-4">
                  {artifact.draft ? (
                    <>
                      <section>
                        <p className="mb-1 text-[10px] font-medium text-foreground-subtle">
                          Item Name
                        </p>
                        <p className="text-sm font-medium leading-relaxed text-foreground">
                          {artifact.draft.item_name}
                        </p>
                      </section>
                      <section>
                        <p className="mb-1 text-[10px] font-medium text-foreground-subtle">
                          Bullet Points
                        </p>
                        <ul className="space-y-1.5">
                          {artifact.draft.bullet_point.map((b, i) => (
                            <li
                              key={i}
                              className="flex gap-2 text-[13px] leading-relaxed text-foreground-muted"
                            >
                              <span className="shrink-0 text-accent">•</span>
                              <span>{b}</span>
                            </li>
                          ))}
                        </ul>
                      </section>
                      <section>
                        <p className="mb-1 text-[10px] font-medium text-foreground-subtle">
                          Description
                        </p>
                        <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-foreground-muted">
                          {artifact.draft.product_description}
                        </p>
                      </section>
                    </>
                  ) : (
                    <pre className="whitespace-pre-wrap text-xs leading-relaxed text-foreground-muted">
                      {artifact.streamedDraft}
                    </pre>
                  )}
                </div>
              </aside>
            )}
          </div>
        )}
      </main>
    </div>
  );
}