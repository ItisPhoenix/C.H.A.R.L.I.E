"use client";

import { useCallback, useEffect, useRef, useState, useMemo, type ReactElement } from "react";
import { useRouter } from "next/navigation";
import { useCharlieStore, rgba, type Session, type Message, type AgentRun, type ToolActivityEntry } from "../store/useCharlieStore";
import { useCharlieSocket } from "../hooks/useCharlieSocket";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { ToastContainer } from "../components/ToastContainer";
import { CommandPalette } from "../components/CommandPalette";
import { ChatView } from "../components/ChatView";
import { InsightRail } from "../components/InsightRail";
import { EventLog } from "../components/EventLog";
import { VoiceDock } from "../components/VoiceDock";
import { TopBar } from "../components/TopBar";
import { Sidebar } from "../components/Sidebar";
import { DesktopFrameView } from "../components/DesktopFrameView";
import {
  MemoriesView, HardwareView, FilesView, ServicesView, OllamaView, ExtensionsView, SkillsView, AgentsView, MCPCenterView
} from "../components/DashboardPanels";

export default function Page(): ReactElement {
  const router = useRouter();
  const currentSessionIdRef = useRef("");
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
  const activeModel = useCharlieStore((s) => s.activeModel);

  const setSessions = useCharlieStore((s) => s.setSessions);
  const setAgentRuns = useCharlieStore((s) => s.setAgentRuns);
  const setCurrentSessionId = useCharlieStore((s) => s.setCurrentSessionId);
  const setMessages = useCharlieStore((s) => s.setMessages);
  const addMessage = useCharlieStore((s) => s.addMessage);
  const setMessagesLoading = useCharlieStore((s) => s.setMessagesLoading);
  const setExecutionTraces = useCharlieStore((s) => s.setExecutionTraces);
  const setVoiceState = useCharlieStore((s) => s.setVoiceState);
  const setAudio = useCharlieStore((s) => s.setAudio);
  const setMic = useCharlieStore((s) => s.setMic);
  const setLaunchId = useCharlieStore((s) => s.setLaunchId);
  const setDesktopControlEnabled = useCharlieStore((s) => s.setDesktopControlEnabled);
  const setAccentColor = useCharlieStore((s) => s.setAccentColor);
  const setActiveModel = useCharlieStore((s) => s.setActiveModel);
  const setVisionModel = useCharlieStore((s) => s.setVisionModel);

  // Router Pages state
  const [activePage, setActivePage] = useState<string>("chats");
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  // Search filter query
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  // Model selection state
  const [modelOpen, setModelOpen] = useState(false);
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
  }, [setActiveModel]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data fetch on mount
    fetchModels();
  }, [fetchModels]);

  // Single source of truth for LLM_MODEL/VISION_LLM_MODEL: this effect is the
  // only /api/config poller (page.tsx is always mounted, unlike InsightRail,
  // which used to run its own independent 10s poll of the same endpoint --
  // see CLAUDE.md 8.5's "duplicate activeModel source" note).
  useEffect(() => {
    let cancelled = false;
    async function pollConfig() {
      try {
        const res = await fetch("/api/config");
        if (!res.ok || cancelled) return;
        const data = await res.json() as { fields?: { key: string; value: unknown }[] };
        const fields = data.fields || [];
        const llm = fields.find((f) => f.key === "LLM_MODEL");
        const vision = fields.find((f) => f.key === "VISION_LLM_MODEL");
        if (llm?.value) setActiveModel(String(llm.value));
        if (vision?.value) setVisionModel(String(vision.value));
      } catch {
        // ignore
      }
    }
    pollConfig();
    const interval = setInterval(pollConfig, 10000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [setActiveModel, setVisionModel]);

  // Notification bell popover state
  const [bellOpen, setBellOpen] = useState(false);

  // Command palette (Ctrl+K) state
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Debounced search logic -- 200ms is standard responsive debounce (was 1500ms).
  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedSearch(searchQuery);
    }, 200);
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

  const { sendWS, isSocketOpen, isStreaming } = useCharlieSocket({
    currentSessionId,
    fetchSessions,
    fetchMessages,
    announceActiveSession,
  });

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
      // activeModel/visionModel are populated by the /api/config poll effect above.
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
        announceActiveSession(currentSessionIdRef.current);
      }
    };
    document.addEventListener("visibilitychange", reclaim);
    window.addEventListener("focus", reclaim);
    return () => {
      document.removeEventListener("visibilitychange", reclaim);
      window.removeEventListener("focus", reclaim);
    };
  }, [announceActiveSession]);

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

    if (!isSocketOpen() && !isStreaming(sid)) {
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
          <div className="bg-status-error/15 border-b border-status-error/25 px-6 py-2 flex items-center justify-between text-[11px] font-mono font-bold anim-rise relative z-40 select-none">
            <span className="text-status-error uppercase tracking-widest flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-status-error animate-ping" />
              WebSocket Connection Interrupted. Retrying active socket...
            </span>
          </div>
        )}

        <TopBar
          mobileMenuOpen={mobileMenuOpen}
          onToggleMobileMenu={() => setMobileMenuOpen(!mobileMenuOpen)}
          activeModel={activeModel}
          modelOpen={modelOpen}
          onToggleModelOpen={() => setModelOpen(!modelOpen)}
          reloadingModel={reloadingModel}
          modelSearchQuery={modelSearchQuery}
          onModelSearchChange={setModelSearchQuery}
          filteredModels={filteredModels}
          onSelectModel={handleModelSelect}
          mic={mic}
          onToggleMic={() => sendMicControl({ mic_muted: !mic.mic_muted })}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          bellOpen={bellOpen}
          onToggleBell={() => setBellOpen(!bellOpen)}
          alerts={alerts}
        />

        {/* Main core layout panel */}
        <div className="flex-1 flex overflow-hidden z-10 p-4 pb-2 gap-4 relative">
          <Sidebar
            mobileMenuOpen={mobileMenuOpen}
            onToggleMobileMenu={() => setMobileMenuOpen((open) => !open)}
            activePage={activePage}
            onSelectPage={setActivePage}
            searchedSessions={searchedSessions}
            currentSessionId={currentSessionId}
            onSelectSession={handleSelectSession}
            onCreateSession={() => handleCreateSession("New Chat")}
            onRenameSession={handleRenameSession}
            onDeleteSession={handleDeleteSession}
            onExportHistory={handleExportHistory}
            accentColor={accentColor}
            onSetAccentColor={setAccentColor}
          />

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
            
            {activePage === "desktop" && <DesktopFrameView />}
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
