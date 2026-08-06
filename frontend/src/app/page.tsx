"use client";

import { useCallback, useEffect, useRef, useState, useMemo, type ReactElement } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  MessageSquare, Monitor, Database, Cpu, Settings, Shield, Bell, Search, Mic, MicOff,
  FolderGit, Network, RefreshCw, Check, X, Menu, Server, Puzzle, GitBranch, ChevronDown, Cable, Sparkles
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useCharlieStore, rgba, lighten, type Session, type Message, type AgentRun, type ToolActivityEntry } from "../store/useCharlieStore";

interface WSMessage {
  type: string;
  session_id?: string;
  payload?: Record<string, unknown> & { session_id?: string };
}
import { ErrorBoundary } from "../components/ErrorBoundary";
import { ToastContainer } from "../components/ToastContainer";
import { SessionRail } from "../components/SessionRail";
import { CommandPalette } from "../components/CommandPalette";
import { ChatView } from "../components/ChatView";
import { InsightRail } from "../components/InsightRail";
import { EventLog } from "../components/EventLog";
import { VoiceDock } from "../components/VoiceDock";
import {
  MemoriesView, HardwareView, FilesView, ServicesView, OllamaView, ExtensionsView, SkillsView, AgentsView, MCPCenterView
} from "../components/WipPages";

function getSessionId(msg: WSMessage): string | undefined {
  return msg.session_id || msg.payload?.session_id || undefined;
}

interface NavButtonProps {
  icon: LucideIcon;
  label: string;
  active: boolean;
  onClick: () => void;
}

/** Sidebar nav item -- one definition instead of the same block copy-pasted
 * per page, and wired to the live --accent token so the active-state color
 * actually follows the user's chosen accent instead of a hardcoded teal. */
function NavButton({ icon: Icon, label, active, onClick }: NavButtonProps): ReactElement {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left rounded-lg px-2.5 py-2 text-xs flex items-center gap-2.5 font-medium cursor-pointer transition ${
        active
          ? "bg-white/5 text-accent font-semibold border-l-2 border-accent"
          : "text-slate-400 hover:text-slate-200 hover:bg-white/5 active:scale-[0.98]"
      }`}
    >
      <Icon className="w-4 h-4 shrink-0" />
      {label}
    </button>
  );
}

export default function Page(): ReactElement {
  const router = useRouter();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const wsStreamingRef = useRef<Set<string>>(new Set());

  const currentSessionIdRef = useRef("");
  const fetchSessionsRef = useRef<() => Promise<Session[]>>(async () => []);
  const fetchMessagesRef = useRef<(id: string) => Promise<void>>(async () => {});
  const announceActiveSessionRef = useRef<(id: string) => void>(() => {});
  const connectWSRef = useRef<() => void>(() => {});
  const abortSessionsRef = useRef<AbortController | null>(null);
  const abortMessagesRef = useRef<AbortController | null>(null);

  // Zustand Store mappings
  const connected = useCharlieStore((s) => s.connected);
  const sessions = useCharlieStore((s) => s.sessions);
  const currentSessionId = useCharlieStore((s) => s.currentSessionId);
  const messages = useCharlieStore((s) => s.messages);
  const messagesLoading = useCharlieStore((s) => s.messagesLoading);
  const alerts = useCharlieStore((s) => s.alerts);
  const voiceState = useCharlieStore((s) => s.voiceState);
  const audio = useCharlieStore((s) => s.audio);
  const mic = useCharlieStore((s) => s.mic);
  const toolActivity = useCharlieStore((s) => s.toolActivity);
  const executionTraces = useCharlieStore((s) => s.executionTraces);
  const accentColor = useCharlieStore((s) => s.accentColor);
  const activeProposal = useCharlieStore((s) => s.activeProposal);
  const activeToolApproval = useCharlieStore((s) => s.activeToolApproval);

  
  const setConnected = useCharlieStore((s) => s.setConnected);
  const setSystemStatus = useCharlieStore((s) => s.setSystemStatus);
  const setSessions = useCharlieStore((s) => s.setSessions);
  const setAgentRuns = useCharlieStore((s) => s.setAgentRuns);
  const setCurrentSessionId = useCharlieStore((s) => s.setCurrentSessionId);
  const setMessages = useCharlieStore((s) => s.setMessages);
  const addMessage = useCharlieStore((s) => s.addMessage);
  const setMessagesLoading = useCharlieStore((s) => s.setMessagesLoading);
  const setExecutionTraces = useCharlieStore((s) => s.setExecutionTraces);
  const setVoiceState = useCharlieStore((s) => s.setVoiceState);
  const setListeningTrigger = useCharlieStore((s) => s.setListeningTrigger);
  const setAudio = useCharlieStore((s) => s.setAudio);
  const setMic = useCharlieStore((s) => s.setMic);
  const setQueue = useCharlieStore((s) => s.setQueue);
  const setAudioLevel = useCharlieStore((s) => s.setAudioLevel);
  const appendToolActivity = useCharlieStore((s) => s.appendToolActivity);
  const setLaunchId = useCharlieStore((s) => s.setLaunchId);
  const setDesktopControlEnabled = useCharlieStore((s) => s.setDesktopControlEnabled);
  const setAccentColor = useCharlieStore((s) => s.setAccentColor);

  // Router Pages state
  const [activePage, setActivePage] = useState<string>("chats");
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  // Search filter query
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  // Model selection state
  const [modelOpen, setModelOpen] = useState(false);
  const [activeModel, setActiveModel] = useState("");
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [reloadingModel, setReloadingModel] = useState(false);
  const [modelSearchQuery, setModelSearchQuery] = useState("");

  const filteredModels = useMemo(() => {
    if (!modelSearchQuery.trim()) return availableModels;
    return availableModels.filter((m) =>
      m.toLowerCase().includes(modelSearchQuery.toLowerCase())
    );
  }, [availableModels, modelSearchQuery]);

  const fetchModels = useCallback(async () => {
    try {
      const res = await fetch("/api/models");
      if (res.ok) {
        const data = await res.json();
        if (data.models && data.models.length > 0) setAvailableModels(data.models);
        if (data.active_model) setActiveModel(data.active_model);
      }
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data fetch on mount
    fetchModels();
  }, [fetchModels]);

  // Notification bell popover state
  const [bellOpen, setBellOpen] = useState(false);

  // Command palette (Ctrl+K) state
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Debounced search logic
  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedSearch(searchQuery);
    }, 1500);
    return () => clearTimeout(handler);
  }, [searchQuery]);

  // Fetch helper
  const fetchJson = useCallback(async (url: string, signal?: AbortSignal) => {
    try {
      const res = await fetch(url, { signal });
      if (res.ok) return await res.json();
    } catch {
      return null;
    }
  }, []);

  // Fetch list of sessions
  const fetchSessions = useCallback(async () => {
    abortSessionsRef.current?.abort();
    const ctrl = new AbortController();
    abortSessionsRef.current = ctrl;
    
    const data = await fetchJson("/api/sessions", ctrl.signal);
    const list = (data as { sessions: Session[] } | null)?.sessions || [];
    setSessions(list);
    return list;
  }, [fetchJson, setSessions]);

  useEffect(() => {
    fetchSessionsRef.current = fetchSessions;
  }, [fetchSessions]);

  // Hydrate persisted sub-agent runs so the Agents page survives a refresh/restart.
  const fetchAgents = useCallback(async () => {
    const data = await fetchJson("/api/agents?limit=100") as { agents: (Omit<AgentRun, "spawnedAt" | "finishedAt"> & { spawnedAt: string; finishedAt: string | null })[] } | null;
    const list = (data?.agents || []).map((a) => ({
      ...a,
      spawnedAt: new Date(a.spawnedAt).getTime(),
      finishedAt: a.finishedAt ? new Date(a.finishedAt).getTime() : undefined,
    }));
    setAgentRuns(list);
  }, [fetchJson, setAgentRuns]);

  const handleCreateSession = useCallback(async (title: string = "New Chat") => {
    try {
      const res = await fetch("/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      if (res.ok) {
        const data = await res.json();
        const updated = await fetchSessions();
        if (data.session_id) setCurrentSessionId(data.session_id);
        else if (updated.length > 0) setCurrentSessionId(updated[0].id);
      }
    } catch {
      // ignore
    }
  }, [fetchSessions, setCurrentSessionId]);

  // Fetch messages in session
  const fetchMessages = useCallback(async (sid: string) => {
    if (!sid) return;
    // Clear stale messages immediately so old session content never flashes
    setMessages([]);
    setMessagesLoading(true);
    abortMessagesRef.current?.abort();
    const ctrl = new AbortController();
    abortMessagesRef.current = ctrl;
    
    const [data, eventsData] = await Promise.all([
      fetchJson(`/api/sessions/${sid}/messages`, ctrl.signal),
      fetchJson(`/api/sessions/${sid}/tool_events`, ctrl.signal),
    ]);
    // Only commit if this session is still the active one
    if (useCharlieStore.getState().currentSessionId !== sid) return;
    setMessages((data as { messages: Message[] } | null)?.messages || []);
    const events = (eventsData as { events: (ToolActivityEntry & { turnId: string | null })[] } | null)?.events || [];
    const traces: Record<string, ToolActivityEntry[]> = {};
    for (const { turnId, ...entry } of events) {
      if (!turnId) continue;
      (traces[turnId] ??= []).push(entry);
    }
    setExecutionTraces(traces);
    setMessagesLoading(false);
  }, [fetchJson, setMessages, setMessagesLoading, setExecutionTraces]);

  useEffect(() => {
    fetchMessagesRef.current = fetchMessages;
  }, [fetchMessages]);

  // Announce active session visually
  const announceActiveSession = useCallback(async (sid: string) => {
    if (document.visibilityState !== "visible") return;
    try {
      await fetch("/api/session/active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid }),
      });
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    announceActiveSessionRef.current = announceActiveSession;
  }, [announceActiveSession]);

  // Send WS packet helper
  const sendWS = useCallback((data: { type: string; payload?: Record<string, unknown> }) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  // Connect WebSocket connection
  const connectWS = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState !== WebSocket.CLOSED) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws`;
    const socket = new WebSocket(wsUrl);
    wsRef.current = socket;

    socket.onopen = () => {
      setConnected(true);
      reconnectAttemptsRef.current = 0;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (currentSessionIdRef.current) {
        announceActiveSessionRef.current(currentSessionIdRef.current);
        fetchMessagesRef.current(currentSessionIdRef.current);
        setTimeout(() => {
          announceActiveSessionRef.current(currentSessionIdRef.current);
        }, 250);
      }
    };

    socket.onclose = () => {
      setConnected(false);
      if (wsRef.current === socket) wsRef.current = null;
      const attempt = reconnectAttemptsRef.current++;
      const delay = Math.min(3000 * 2 ** attempt, 30000);
      reconnectTimeoutRef.current = setTimeout(() => connectWSRef.current?.(), delay);
    };

    socket.onerror = () => {
      socket.close();
    };

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        const store = useCharlieStore.getState();

        if (msg.type === "system_status") {
          setSystemStatus(msg.payload);
        } else if (msg.type === "vad_start" || msg.type === "wake_word") {
          setVoiceState("listening");
          setListeningTrigger(msg.type === "wake_word" ? "wake_word" : "vad");
        } else if (msg.type === "thinking") {
          setVoiceState("thinking");
          setListeningTrigger(null);
        } else if (msg.type === "speaking_start") {
          setVoiceState("speaking");
          setListeningTrigger(null);
        } else if (msg.type === "speaking_stop" || msg.type === "response_done") {
          setVoiceState("idle");
          setListeningTrigger(null);
          if (msg.type === "response_done") {
            const eventSession = getSessionId(msg);
            wsStreamingRef.current.delete(eventSession || currentSessionIdRef.current);
            store.clearToolActivity();
            // Only re-fetch messages if the completing session is still the active one
            const completedSid = eventSession || currentSessionIdRef.current;
            if (completedSid && completedSid === currentSessionIdRef.current) {
              fetchMessagesRef.current(completedSid);
            }
            fetchSessionsRef.current();
          }
        } else if (msg.type === "audio_state") {
          setAudio({
            muted: Boolean(msg.payload?.muted),
            volume: typeof msg.payload?.volume === "number" ? msg.payload.volume : 1.0,
          });
        } else if (msg.type === "mic_state") {
          setMic({ mic_muted: Boolean(msg.payload?.mic_muted) });
        } else if (msg.type === "queue_update") {
          setQueue({
            count: typeof msg.payload?.count === "number" ? msg.payload.count : 0,
            texts: Array.isArray(msg.payload?.texts) ? msg.payload.texts : [],
          });
        } else if (msg.type === "session_updated") {
          const sid = getSessionId(msg);
          const title = msg.title || msg.payload?.title;
          const deleted = msg.payload?.deleted;
          fetchSessionsRef.current();
          if (sid && deleted) {
            const cur = store.sessions;
            setSessions(cur.filter((s) => s.id !== sid));
            if (store.currentSessionId === sid) {
              setCurrentSessionId("");
            }
          } else if (sid && title) {
            const cur = store.sessions;
            setSessions(cur.map((s) => (s.id === sid ? { ...s, title } : s)));
          }
        } else if (msg.type === "audio_level") {
          const level = typeof msg.payload?.level === "number" ? msg.payload.level : 0;
          setAudioLevel(Math.max(0, Math.min(1, level)));
        } else if (msg.type === "log") {
          store.addLog(msg.payload?.line || "");
        } else if (msg.type === "alert") {
          store.addAlert({
            severity: msg.payload?.severity || "info",
            message: msg.payload?.message || "",
            timestamp: new Date().toLocaleTimeString(),
          });
        } else if (msg.type === "tool_call") {
          const eventSession = getSessionId(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          appendToolActivity({ kind: "tool_call", name: msg.payload?.name || "tool", text: msg.payload?.text || "", sessionId: eventSession });
        } else if (msg.type === "tool_result") {
          const eventSession = getSessionId(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          appendToolActivity({ kind: "tool_result", name: msg.payload?.name || "tool", text: msg.payload?.text || "", sessionId: eventSession });
        } else if (msg.type === "thinking_update") {
          const eventSession = getSessionId(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          appendToolActivity({ kind: "thinking_update", name: "thinking", text: msg.payload?.text || "", sessionId: eventSession });
        } else if (msg.type === "agent_spawned") {
          const eventSession = getSessionId(msg);
          const agentId = msg.payload?.agent_id || "";
          const task = msg.payload?.task || "";
          if (!eventSession || eventSession === currentSessionIdRef.current) {
            appendToolActivity({ kind: "agent_spawned", name: agentId, text: task, sessionId: eventSession });
          }
          store.upsertAgentRun({ agentId, task, status: "running", spawnedAt: Date.now(), sessionId: eventSession });
        } else if (msg.type === "agent_status") {
          const eventSession = getSessionId(msg);
          const agentId = msg.payload?.agent_id || "";
          const toolName = msg.payload?.tool_name || "";
          if (!eventSession || eventSession === currentSessionIdRef.current) {
            appendToolActivity({ kind: "agent_status", name: agentId, text: toolName, sessionId: eventSession });
          }
          store.upsertAgentRun({ agentId, lastTool: toolName });
        } else if (msg.type === "agent_result") {
          const eventSession = getSessionId(msg);
          const agentId = msg.payload?.agent_id || "";
          const result = msg.payload?.result || "";
          if (!eventSession || eventSession === currentSessionIdRef.current) {
            appendToolActivity({ kind: "agent_result", name: agentId, text: result, sessionId: eventSession });
          }
          const status = result.includes("timed out") ? "timeout" : result.includes("cancelled") ? "cancelled" : "done";
          store.upsertAgentRun({ agentId, result, status, finishedAt: Date.now() });
        } else if (msg.type === "agent_cancel_ack") {
          if (!msg.payload?.found) {
            store.addAlert({
              severity: "warn",
              message: "Could not cancel: agent already finished or not found.",
              timestamp: new Date().toLocaleTimeString(),
            });
          }
        } else if (msg.type === "transcript") {
          const eventSession = getSessionId(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          const spoken = (msg.payload?.text || "").trim();
          if (spoken) {
            addMessage({ role: "user", content: spoken });
          }
        } else if (msg.type === "desktop_frame") {
          const eventSession = getSessionId(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          store.setLatestDesktopFrame({
            sessionId: eventSession || currentSessionIdRef.current,
            imageB64: msg.payload?.image_b64 || "",
            marks: msg.payload?.marks || [],
            receivedAt: Date.now(),
          });
        } else if (msg.type === "recovery_proposal") {
          const eventSession = getSessionId(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          store.setActiveProposal(msg.payload);
        } else if (msg.type === "tool_approval_request") {
          const eventSession = getSessionId(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          store.setActiveToolApproval(msg.payload);
        } else if (msg.type === "token") {
          const eventSession = getSessionId(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          wsStreamingRef.current.add(eventSession || currentSessionIdRef.current);
          store.updateLastMessageContent(msg.payload?.text || "");
        }
      } catch {
        // ignore
      }
    };
  }, [setConnected, setSystemStatus, setVoiceState, setListeningTrigger, setAudio, setMic, setQueue, setAudioLevel, appendToolActivity, addMessage, setSessions, setCurrentSessionId]);

  useEffect(() => { connectWSRef.current = connectWS; });
  useEffect(() => { currentSessionIdRef.current = currentSessionId; }, [currentSessionId]);

  const handleStop = useCallback(() => {
    setVoiceState("idle");
    setMessagesLoading(false);
    sendWS({ type: "stop" });
  }, [setVoiceState, setMessagesLoading, sendWS]);

  // Global hotkeys handler
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setPaletteOpen(true);
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "n") {
        e.preventDefault();
        handleCreateSession("New Chat");
      }
      if (e.key === "Escape") {
        handleStop();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleCreateSession, handleStop]);

  // Apply the persisted accent color after mount, so the first client render matches
  // the server-rendered HTML (avoids a hydration mismatch) before diverging to the saved value.
  useEffect(() => {
    const saved = window.localStorage.getItem("charlie_accent");
    if (saved) setAccentColor(saved);
  }, [setAccentColor]);

  // Sync details on boot
  useEffect(() => {
    const init = async () => {
      const status = await fetchJson("/api/status") as { launch_id?: string; desktop_control_enabled?: boolean } | null;
      const lid = status && typeof status.launch_id === "string" ? status.launch_id : "";
      if (lid) setLaunchId(lid);
      setDesktopControlEnabled(Boolean(status?.desktop_control_enabled));

      if (!useCharlieStore.getState().currentSessionId) {
        const storedLastSid = typeof window !== "undefined" ? window.localStorage.getItem("charlie_last_session") : null;
        const existingSessions = await fetchSessions();
        const lastSessionStillValid = Boolean(storedLastSid && existingSessions.some((s) => s.id === storedLastSid));

        if (lastSessionStillValid && storedLastSid) {
          setCurrentSessionId(storedLastSid);
        } else {
          await handleCreateSession("New Chat");
        }
      } else {
        await fetchSessions();
      }

      await fetchAgents();

      const audioState = await fetchJson("/api/audio") as { muted?: boolean; volume?: number } | null;
      if (audioState) {
        setAudio({
          muted: Boolean(audioState.muted),
          volume: audioState.volume ?? 1.0,
        });
      }
      const micState = await fetchJson("/api/mic") as { mic_muted?: boolean } | null;
      if (micState && typeof micState.mic_muted === "boolean") {
        setMic({ mic_muted: micState.mic_muted });
      }

      const rConfig = await fetch("/api/config");
      if (rConfig.ok) {
        const data = await rConfig.json() as { fields?: { key: string; value: unknown }[] };
        const llm = (data.fields || []).find((f) => f.key === "LLM_MODEL");
        if (llm?.value) setActiveModel(String(llm.value));
      }
    };
    void init();
  }, [fetchSessions, fetchAgents, handleCreateSession, setAudio, setMic, fetchJson, setLaunchId, setDesktopControlEnabled, setCurrentSessionId]);

  // Sync active sessions
  useEffect(() => {
    if (currentSessionId) {
      fetchMessages(currentSessionId);
      announceActiveSession(currentSessionId);
      // Persists across restarts (unlike launch_id, which is a fresh UUID
      // every process start) so the app resumes the last chat like Claude
      // web does, instead of always landing on a brand new one.
      window.localStorage.setItem("charlie_last_session", currentSessionId);
    }
  }, [currentSessionId, fetchMessages, announceActiveSession]);

  // Visibility focus routing reclaim
  useEffect(() => {
    const reclaim = () => {
      if (document.visibilityState === "visible" && currentSessionIdRef.current) {
        announceActiveSessionRef.current(currentSessionIdRef.current);
      }
    };
    document.addEventListener("visibilitychange", reclaim);
    window.addEventListener("focus", reclaim);
    return () => {
      document.removeEventListener("visibilitychange", reclaim);
      window.removeEventListener("focus", reclaim);
    };
  }, []);

  useEffect(() => {
    connectWS();
    return () => {
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
    };
  }, [connectWS]);

  useEffect(() => {
    return () => {
      abortSessionsRef.current?.abort();
      abortMessagesRef.current?.abort();
    };
  }, []);

  // Send message API
  const handleSendMessage = async (text: string) => {
    if (!currentSessionId) return;
    const sid = currentSessionId;
    addMessage({ role: "user", content: text });
    sendWS({ type: "chat", payload: { session_id: sid, text } });

    if (!(wsRef.current && wsRef.current.readyState === WebSocket.OPEN) && !wsStreamingRef.current.has(sid)) {
      try {
        const res = await fetch(`/api/sessions/${sid}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        if (!res.ok) {
          addMessage({ role: "assistant", content: "Connection failure. Please retry." });
        }
      } catch {
        addMessage({ role: "assistant", content: "Connection failure. Please retry." });
      }
    }
  };

  const handleApproveRecovery = (proposalId: string) => {
    sendWS({ type: "recovery_approve", payload: { proposal_id: proposalId } });
  };

  const handleRejectRecovery = (proposalId: string) => {
    sendWS({ type: "recovery_reject", payload: { proposal_id: proposalId } });
  };

  const handleApproveToolCall = (requestId: string) => {
    sendWS({ type: "tool_approve", payload: { request_id: requestId } });
  };

  const handleRejectToolCall = (requestId: string) => {
    sendWS({ type: "tool_reject", payload: { request_id: requestId } });
  };

  const handleSelectSession = useCallback((id: string) => {
    // Re-selecting the already-active session is a no-op for React's state (same
    // primitive), so the currentSessionId effect never re-fires -- force the refetch.
    if (id === currentSessionId) {
      fetchMessages(id);
      return;
    }
    setMessages([]);
    setCurrentSessionId(id);
  }, [currentSessionId, fetchMessages, setMessages, setCurrentSessionId]);

  const handleExportHistory = useCallback(async () => {
    try {
      const res = await fetch("/api/history?limit=1000");
      if (!res.ok) return;
      const data = await res.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `charlie-history-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // ignore
    }
  }, []);

  const sendAudioControl = useCallback((patch: { muted?: boolean; volume?: number }) => {
    const currentAudio = useCharlieStore.getState().audio;
    setAudio({ ...currentAudio, ...patch });
    sendWS({ type: "audio_control", payload: patch });
  }, [sendWS, setAudio]);

  const sendMicControl = useCallback((patch: { mic_muted: boolean }) => {
    setMic({ mic_muted: patch.mic_muted });
    sendWS({ type: "mic_control", payload: patch });
  }, [sendWS, setMic]);

  const handleRenameSession = async (id: string, title: string) => {
    try {
      const res = await fetch(`/api/sessions/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      if (res.ok) await fetchSessions();
    } catch {
      // ignore
    }
  };

  const handleDeleteSession = useCallback(async (id: string) => {
    try {
      const res = await fetch(`/api/sessions/${id}`, { method: "DELETE" });
      if (res.ok) {
        const updated = await fetchSessions();
        if (currentSessionId === id) {
          const remaining = updated.filter((s) => s.id !== id);
          setCurrentSessionId(remaining.length > 0 ? remaining[0].id : "");
        }
      }
    } catch {
      // ignore
    }
  }, [fetchSessions, currentSessionId, setCurrentSessionId]);

  // Model switching core API
  const handleModelSelect = async (modelId: string) => {
    setActiveModel(modelId);
    setModelOpen(false);
    setReloadingModel(true);
    
    useCharlieStore.getState().addAlert({
      severity: "info",
      message: `Configuring model ${modelId}... Reloading engine core...`,
      timestamp: new Date().toLocaleTimeString(),
    });

    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ LLM_MODEL: modelId }),
      });
      if (res.ok) {
        const reloadRes = await fetch("/api/config/reload", { method: "POST" });
        if (reloadRes.ok) {
          useCharlieStore.getState().addAlert({
            severity: "info",
            message: `Engine core successfully reloaded with model ${modelId}.`,
            timestamp: new Date().toLocaleTimeString(),
          });
        }
      }
    } catch {
      // ignore
    } finally {
      setReloadingModel(false);
    }
  };

  // Global filtered sessions list based on global search
  const searchedSessions = useMemo(() => {
    if (!debouncedSearch) return sessions;
    return sessions.filter((s) => s.title.toLowerCase().includes(debouncedSearch.toLowerCase()));
  }, [sessions, debouncedSearch]);

  const canvasBg = `radial-gradient(1200px 700px at 12% -8%, ${rgba(accentColor, 0.12)}, transparent 60%), radial-gradient(1000px 600px at 105% 10%, ${rgba(accentColor, 0.06)}, transparent 55%), var(--color-canvas)`;

  return (
    <ErrorBoundary>
      <div 
        style={{ background: canvasBg }}
        className="h-screen w-screen flex flex-col overflow-hidden relative font-sans text-[var(--color-text-primary)]"
      >
        <ToastContainer />

        {/* Ambient offline warning banner */}
        {!connected && (
          <div className="bg-red-500/15 border-b border-red-500/25 px-6 py-2 flex items-center justify-between text-[11px] font-mono font-bold animate-[slideDown_0.2s_ease-out] relative z-40 select-none">
            <span className="text-red-400 uppercase tracking-widest flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-ping" />
              WebSocket Connection Interrupted. Retrying active socket...
            </span>
          </div>
        )}

        {/* Top Bar Navigation Dashboard */}
        <header className="px-6 py-3 bg-zinc-950/80 border-b border-[var(--color-glass-border)] flex items-center justify-between z-30 shrink-0 select-none">
          <div className="flex items-center gap-6">
            <button
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="md:hidden p-1.5 rounded-lg border border-[var(--color-glass-border)] text-slate-300 hover:text-slate-100 hover:bg-zinc-900 transition active:scale-[0.98] cursor-pointer"
              aria-label={mobileMenuOpen ? "Close session menu" : "Open session menu"}
            >
              {mobileMenuOpen ? <X className="w-4 h-4" /> : <Menu className="w-4 h-4" />}
            </button>
            <div className="flex items-center gap-2">
              <div className="w-6 h-6 rounded-lg bg-accent flex items-center justify-center font-display text-black font-extrabold text-sm shadow-[0_0_12px_var(--accent-border)]">
                C
              </div>
              <div>
                <h1 className="font-display font-bold uppercase tracking-wider text-xs">
                  CHARLIE
                </h1>
                <p className="text-[10px] font-mono text-slate-500 tracking-widest uppercase">
                  AI OS dashboard
                </p>
              </div>
            </div>

            {/* Active Model Selector */}
            <div className="relative">
              <button
                onClick={() => setModelOpen(!modelOpen)}
                disabled={reloadingModel}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--color-glass-border)] bg-zinc-900/40 text-xs font-semibold text-slate-300 hover:text-slate-100 hover:bg-zinc-900 transition active:scale-[0.98] cursor-pointer"
              >
                {reloadingModel ? (
                  <RefreshCw className="w-3.5 h-3.5 text-cyan-400 animate-spin" />
                ) : (
                  <Shield className="w-3.5 h-3.5 text-slate-400" />
                )}
                <span className="font-mono text-[10px] truncate max-w-[120px]">{activeModel}</span>
              </button>

              {modelOpen && (
                <div className="absolute top-9 left-0 z-50 w-64 rounded-xl bg-zinc-950/95 border border-[var(--color-glass-border)] p-2 shadow-2xl animate-[rise_0.15s_ease-out] space-y-1.5">
                  <div className="relative px-1">
                    <Search className="absolute left-3 top-2.5 w-3 h-3 text-slate-500" />
                    <input
                      type="text"
                      value={modelSearchQuery}
                      onChange={(e) => setModelSearchQuery(e.target.value)}
                      placeholder="Filter API key / local models..."
                      className="w-full bg-zinc-900 border border-white/10 rounded-md pl-7 pr-2 py-1 text-[10px] text-slate-200 placeholder:text-slate-500 outline-none font-mono"
                      autoFocus
                    />
                  </div>
                  <div className="max-h-60 overflow-y-auto scrollbar space-y-0.5">
                    {filteredModels.length === 0 ? (
                      <div className="py-3 text-center text-[10px] font-mono text-slate-500">No models match &quot;{modelSearchQuery}&quot;</div>
                    ) : (
                      filteredModels.map((model) => (
                        <button
                          key={model}
                          onClick={() => handleModelSelect(model)}
                          className="w-full text-left font-mono text-[10px] text-slate-300 hover:text-slate-100 px-2.5 py-1.5 rounded-lg hover:bg-white/5 flex items-center justify-between cursor-pointer"
                        >
                          <span className="truncate pr-2">{model}</span>
                          {activeModel === model && <Check className="w-3.5 h-3.5 text-cyan-400 shrink-0" />}
                        </button>
                      ))
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Microphone VAD capsule */}
            <button
              onClick={() => sendMicControl({ mic_muted: !mic.mic_muted })}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-[var(--color-glass-border)] bg-zinc-900/40 text-xs font-semibold text-slate-300 hover:text-slate-100 transition active:scale-[0.98]"
            >
              {mic.mic_muted ? (
                <>
                  <MicOff className="w-3.5 h-3.5 text-slate-500" />
                  <span className="text-[10px] text-slate-500 font-mono">MUTED</span>
                </>
              ) : (
                <>
                  <Mic className="w-3.5 h-3.5 text-cyan-400 animate-pulse" />
                  <span className="text-[10px] text-cyan-400 font-mono">LISTENING</span>
                </>
              )}
            </button>
          </div>

          {/* Global Search & Notifications */}
          <div className="flex items-center gap-4">
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 w-3.5 h-3.5 text-slate-500 pointer-events-none" />
              <input
                id="global-search-input"
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Global search (Ctrl+K)"
                className="w-56 bg-zinc-900/60 border border-[var(--color-glass-border)] rounded-lg pl-8 pr-3 py-1.5 text-xs text-[var(--color-text-primary)] placeholder:text-slate-500 outline-none transition focus:border-[var(--color-glass-border-hover)]"
              />
            </div>

            {/* Notification Bell */}
            <div className="relative select-none">
              <button
                onClick={() => setBellOpen(!bellOpen)}
                className="relative rounded-lg w-8 h-8 grid place-items-center text-slate-400 hover:text-slate-100 hover:bg-white/5 active:scale-95 transition"
                aria-label="System Alerts"
              >
                <Bell className="w-4 h-4" />
                {alerts.length > 0 && (
                  <span className="absolute top-1 right-1.5 w-2 h-2 rounded-full bg-cyan-400" />
                )}
              </button>

              {bellOpen && (
                <div className="absolute top-9 right-0 z-50 w-72 rounded-xl bg-zinc-950 border border-[var(--color-glass-border)] p-3 shadow-2xl animate-[rise_0.15s_ease-out] font-mono text-[10px]">
                  <div className="flex items-center justify-between border-b border-white/5 pb-2 mb-2">
                    <span className="font-bold text-slate-400 uppercase tracking-widest">Recent Alerts</span>
                    <button
                      onClick={() => useCharlieStore.setState({ alerts: [] })}
                      className="text-slate-500 hover:text-red-400 hover:bg-white/5 p-0.5 rounded"
                    >
                      Clear
                    </button>
                  </div>
                  {alerts.length === 0 ? (
                    <p className="text-slate-500 italic py-4 text-center">No alerts logged.</p>
                  ) : (
                    <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1 scrollbar">
                      {alerts.map((alert, i) => (
                        <div key={i} className="p-1.5 bg-zinc-900 border border-white/5 rounded">
                          <p className={`font-semibold ${alert.severity === "error" ? "text-red-400" : "text-slate-300"}`}>
                            {alert.message}
                          </p>
                          <span className="text-[10px] text-slate-500 block mt-1">{alert.timestamp}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </header>

        {/* Main core layout panel */}
        <div className="flex-1 flex overflow-hidden z-10 p-4 pb-2 gap-4 relative">
          
          {/* Column 1: Left Navigation Sidebar */}
          <nav
            className={`shrink-0 border-r border-[var(--color-glass-border)] bg-zinc-950/20 p-4 flex flex-col justify-between select-none overflow-y-auto scrollbar transition-[width] duration-200 ${
              mobileMenuOpen ? "w-72" : "w-52"
            }`}
          >
            <div className="space-y-6">
              
              {/* Category: MAIN */}
              <div className="space-y-1.5">
                <h3 className="px-2 text-[10px] font-bold text-slate-500 uppercase tracking-widest">
                  Main
                </h3>
                <div className="space-y-0.5">
                  <button
                    onClick={() => {
                      setActivePage("chats");
                      setMobileMenuOpen((open) => !open);
                    }}
                    aria-expanded={mobileMenuOpen}
                    className={`w-full text-left rounded-lg px-2.5 py-2 text-xs flex items-center justify-between gap-2.5 font-medium cursor-pointer transition ${
                      activePage === "chats" ? "bg-white/5 text-slate-100" : "text-slate-400 hover:text-slate-200 hover:bg-white/5"
                    }`}
                  >
                    <span className="flex items-center gap-2.5">
                      <MessageSquare className="w-4 h-4 shrink-0" />
                      Chats
                    </span>
                    <ChevronDown
                      className={`w-3.5 h-3.5 shrink-0 transition-transform ${mobileMenuOpen ? "rotate-180" : ""}`}
                    />
                  </button>
                  {mobileMenuOpen && (
                    <div className="rounded-lg border border-white/5 bg-zinc-900/40 overflow-hidden">
                      <SessionRail
                        variant="accordion"
                        sessions={searchedSessions}
                        currentId={currentSessionId}
                        onSelect={handleSelectSession}
                        onCreate={() => handleCreateSession("New Chat")}
                        onRename={handleRenameSession}
                        onDelete={handleDeleteSession}
                        onExport={handleExportHistory}
                      />
                    </div>
                  )}
                  <NavButton
                    icon={Database}
                    label="Memories"
                    active={activePage === "memories"}
                    onClick={() => setActivePage("memories")}
                  />
                </div>
              </div>

              {/* Category: TOOLS */}
              <div className="space-y-1.5">
                <h3 className="px-2 text-[10px] font-bold text-slate-500 uppercase tracking-widest">
                  Tools
                </h3>
                <div className="space-y-0.5">
                  <NavButton
                    icon={Monitor}
                    label="Desktop"
                    active={activePage === "desktop"}
                    onClick={() => setActivePage("desktop")}
                  />
                  <NavButton
                    icon={FolderGit}
                    label="Files"
                    active={activePage === "files"}
                    onClick={() => setActivePage("files")}
                  />
                  <NavButton
                    icon={Server}
                    label="Services"
                    active={activePage === "docker"}
                    onClick={() => setActivePage("docker")}
                  />
                  <NavButton
                    icon={Network}
                    label="Local Models"
                    active={activePage === "ollama"}
                    onClick={() => setActivePage("ollama")}
                  />
                  <NavButton
                    icon={Puzzle}
                    label="Extensions"
                    active={activePage === "extensions"}
                    onClick={() => setActivePage("extensions")}
                  />
                  <NavButton
                    icon={Sparkles}
                    label="Skills"
                    active={activePage === "skills"}
                    onClick={() => setActivePage("skills")}
                  />
                  <NavButton
                    icon={GitBranch}
                    label="Agents"
                    active={activePage === "agents"}
                    onClick={() => setActivePage("agents")}
                  />
                  <NavButton
                    icon={Cable}
                    label="MCP Servers"
                    active={activePage === "mcp"}
                    onClick={() => setActivePage("mcp")}
                  />
                </div>
              </div>

              {/* Category: SYSTEM */}
              <div className="space-y-1.5">
                <h3 className="px-2 text-[10px] font-bold text-slate-500 uppercase tracking-widest">
                  System
                </h3>
                <div className="space-y-0.5">
                  <NavButton
                    icon={Cpu}
                    label="Hardware"
                    active={activePage === "hardware"}
                    onClick={() => setActivePage("hardware")}
                  />
                  <Link
                    href="/settings"
                    className="w-full text-left rounded-lg px-2.5 py-2 text-xs flex items-center gap-2.5 font-medium cursor-pointer text-slate-400 hover:text-slate-200 hover:bg-white/5 active:scale-[0.98] transition"
                  >
                    <Settings className="w-4 h-4 shrink-0" />
                    Settings
                  </Link>
                </div>
              </div>
            </div>

            {/* Sidebar Footer Accent Dot Pickers */}
            <div className="border-t border-white/5 pt-4 flex flex-col gap-2">
              <span className="px-2 text-[10px] font-mono font-bold tracking-widest text-slate-500 uppercase">
                ACCENT THEME
              </span>
              <div className="flex gap-2 px-2">
                {["#a855f7", "#3b82f6", "#ef4444", "#f59e0b", "#06b6d4"].map((color) => (
                  <button
                    key={color}
                    onClick={() => setAccentColor(color)}
                    className="w-3.5 h-3.5 rounded-full border border-white/20 transition hover:scale-110 cursor-pointer active:scale-90"
                    style={{
                      background: color,
                      outline: accentColor === color ? `1.5px solid ${lighten(color, 0.35)}` : "none",
                      outlineOffset: "1px",
                    }}
                    aria-label={`Set accent to ${color}`}
                  />
                ))}
              </div>
            </div>
          </nav>

          {/* Dynamic Router Viewport Content */}
          <div className="flex-1 flex overflow-hidden h-full">
            {activePage === "chats" && (
              <>
                {/* Session list now lives as a dropdown off the Chats nav item, see above -- no permanent column here. */}

                {/* Middle: Chat Feed Viewport */}
                <main className="flex-1 min-w-0 flex flex-col h-full bg-zinc-900/10">
                  <ChatView
                    messages={messages}
                    onSend={handleSendMessage}
                    onStop={handleStop}
                    loading={messagesLoading}
                    voiceState={voiceState}
                    toolActivity={toolActivity}
                    executionTraces={executionTraces}
                    activeProposal={activeProposal}
                    onApproveRecovery={handleApproveRecovery}
                    onRejectRecovery={handleRejectRecovery}
                    activeToolApproval={activeToolApproval}
                    onApproveTool={handleApproveToolCall}
                    onRejectTool={handleRejectToolCall}
                  />
                </main>

                {/* Right Sidebar widgets */}
                <div className="hidden xl:flex h-full">
                  <InsightRail />
                </div>
              </>
            )}

            {/* Custom WIP dashboard panels */}
            {activePage === "memories" && <MemoriesView />}
            {activePage === "hardware" && <HardwareView />}
            {activePage === "files" && <FilesView />}
            {activePage === "docker" && <ServicesView />}
            {activePage === "ollama" && <OllamaView />}
            {activePage === "extensions" && <ExtensionsView />}
            {activePage === "skills" && <SkillsView />}
            {activePage === "agents" && <AgentsView />}
            {activePage === "mcp" && <MCPCenterView />}
            
            {activePage === "desktop" && (
              <div className="flex-1 bg-zinc-950 p-6 flex flex-col overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
                <div className="border-b border-white/5 pb-3 mb-6">
                  <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
                    <Monitor className="w-5 h-5 text-slate-400" />
                    Desktop control live feed
                  </h2>
                </div>
                <InsightRail />
              </div>
            )}
          </div>
        </div>

        {/* Bottom Console Multi-Tab Log */}
        {activePage === "chats" && (
          <div className="shrink-0 px-1 mt-2">
            <EventLog />
          </div>
        )}

        {/* Bottom Voice Dock Equalizer */}
        <VoiceDock
          state={voiceState}
          connected={connected}
          audio={audio}
          mic={mic}
          onAudioControl={sendAudioControl}
          onMicControl={sendMicControl}
        />

        {paletteOpen && (
          <CommandPalette
            onClose={() => setPaletteOpen(false)}
            sessions={sessions}
            currentSessionId={currentSessionId}
            onJumpToSession={handleSelectSession}
            models={availableModels}
            activeModel={activeModel}
            onSwitchModel={handleModelSelect}
            micMuted={mic.mic_muted}
            onToggleMic={() => sendMicControl({ mic_muted: !mic.mic_muted })}
            audioMuted={audio.muted}
            onToggleAudio={() => sendAudioControl({ muted: !audio.muted })}
            onOpenSettings={() => router.push("/settings")}
            onExportHistory={handleExportHistory}
            onStartBackgroundTask={(text) => sendWS({ type: "background_task_start", payload: { text } })}
          />
        )}
      </div>
    </ErrorBoundary>
  );
}
