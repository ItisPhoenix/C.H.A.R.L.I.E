"use client";

import { useEffect, useCallback, useRef, useState } from "react";
import { useCharlieStore } from "../store/useCharlieStore";
import { SessionRail } from "../components/SessionRail";
import { ChatView } from "../components/ChatView";
import { InsightRail } from "../components/InsightRail";
import { VoiceDock } from "../components/VoiceDock";
import { ErrorBoundary } from "../components/ErrorBoundary";

export default function Page() {
  const connected = useCharlieStore((s) => s.connected);
  const setConnected = useCharlieStore((s) => s.setConnected);
  const systemStatus = useCharlieStore((s) => s.systemStatus);
  const setSystemStatus = useCharlieStore((s) => s.setSystemStatus);
  const sessions = useCharlieStore((s) => s.sessions);
  const setSessions = useCharlieStore((s) => s.setSessions);
  const currentSessionId = useCharlieStore((s) => s.currentSessionId);
  const setCurrentSessionId = useCharlieStore((s) => s.setCurrentSessionId);
  const messages = useCharlieStore((s) => s.messages);
  const setMessages = useCharlieStore((s) => s.setMessages);
  const messagesLoading = useCharlieStore((s) => s.messagesLoading);
  const setMessagesLoading = useCharlieStore((s) => s.setMessagesLoading);
  const addLog = useCharlieStore((s) => s.addLog);
  const addAlert = useCharlieStore((s) => s.addAlert);
  const blackboard = useCharlieStore((s) => s.blackboard);
  const setBlackboard = useCharlieStore((s) => s.setBlackboard);
  const voiceState = useCharlieStore((s) => s.voiceState);
  const setVoiceState = useCharlieStore((s) => s.setVoiceState);
  const audio = useCharlieStore((s) => s.audio);
  const setAudio = useCharlieStore((s) => s.setAudio);
  const mic = useCharlieStore((s) => s.mic);
  const setMic = useCharlieStore((s) => s.setMic);
  const setAudioLevel = useCharlieStore((s) => s.setAudioLevel);
  const updateLastMessageContent = useCharlieStore((s) => s.updateLastMessageContent);
  const addMessage = useCharlieStore((s) => s.addMessage);

  const [railCollapsed, setRailCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectAttemptsRef = useRef<number>(0);
  const abortSessionsRef = useRef<AbortController | null>(null);
  const abortMessagesRef = useRef<AbortController | null>(null);
  const connectWSRef = useRef<(() => void) | null>(null);
  const currentSessionIdRef = useRef<string>("");
  // Separate controllers: a rename-triggered fetchSessions must not abort an
  // in-flight fetchMessages (and vice versa), or the UI gets stuck loading.
  const abortSessions = useCallback(() => {
    abortSessionsRef.current?.abort();
    const controller = new AbortController();
    abortSessionsRef.current = controller;
    return controller.signal;
  }, []);
  const abortMessages = useCallback(() => {
    abortMessagesRef.current?.abort();
    const controller = new AbortController();
    abortMessagesRef.current = controller;
    return controller.signal;
  }, []);

  const sendWS = useCallback((payload: unknown) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
    }
  }, []);

  // Resolve session id from a top-level field or payload nesting.
  const sessionOf = (msg: { session_id?: string; payload?: { session_id?: string } }): string | undefined =>
    msg.session_id || msg.payload?.session_id;

  const fetchJson = useCallback(async (url: string): Promise<unknown | null> => {
    try {
      const r = await fetch(url);
      return r.ok ? await r.json() : null;
    } catch {
      return null;
    }
  }, []);

  // Guards against overlapping fetchMessages calls (rapid session switches
  // would otherwise race and re-render duplicate/stale message lists).
  const fetchMessagesInFlight = useRef<string | null>(null);

  // Fetch all sessions
  const fetchSessions = useCallback(async (): Promise<Array<{id: string}>> => {
    const signal = abortSessions();
    try {
      const res = await fetch("/api/sessions", { signal });
      if (res.ok) {
        const data = await res.json();
        setSessions(data.sessions || []);
        // Only auto-focus the first session if none is active yet. Renames and
        // background refreshes must not yank focus away from the open session.
        if (data.sessions && data.sessions.length > 0 && !useCharlieStore.getState().currentSessionId) {
          setCurrentSessionId(data.sessions[0].id);
        }
        return data.sessions || [];
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return [];
      console.error("Error fetching sessions:", err);
    }
    return [];
  }, [setSessions, setCurrentSessionId, abortSessions]);

  // Fetch messages for active session
  const fetchMessages = useCallback(async (sid: string) => {
    if (!sid || fetchMessagesInFlight.current === sid) return;
    fetchMessagesInFlight.current = sid;
    const signal = abortMessages();
    setMessagesLoading(true);
    try {
      const res = await fetch(`/api/sessions/${sid}/messages`, { signal });
      if (res.ok) {
        const data = await res.json();
        setMessages(
          (data.messages || []).map((m: { role: string; content: string; id?: string }) => ({
            id: crypto.randomUUID(),
            role: m.role,
            content: m.content,
          }))
        );
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      console.error("Error fetching session messages:", err);
    } finally {
      fetchMessagesInFlight.current = null;
      setMessagesLoading(false);
    }
  }, [setMessages, setMessagesLoading, abortMessages]);


  // Connect WebSocket
  const connectWS = useCallback(() => {
    if (wsRef.current) return;

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
      // Sync active session on (re)connect, then pull the latest transcript
      // so the UI self-heals after a dropout without a manual page refresh.
      if (currentSessionIdRef.current) {
        socket.send(JSON.stringify({ type: "session_active", payload: { session_id: currentSessionIdRef.current } }));
        fetchMessages(currentSessionIdRef.current);
      }
    };

    socket.onclose = () => {
      setConnected(false);
      wsRef.current = null;
      const attempt = reconnectAttemptsRef.current++;
      const delay = Math.min(3000 * 2 ** attempt, 30000);
      reconnectTimeoutRef.current = setTimeout(() => connectWSRef.current?.(), delay);
    };

    socket.onerror = (err) => {
      console.error("WebSocket error:", err);
      socket.close();
    };

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        
        // Handle telemetry and status updates
        if (msg.type === "system_status") {
          setSystemStatus(msg.payload);
        } else if (msg.type === "blackboard_update") {
          setBlackboard(msg.payload);
        } else if (msg.type === "vad_start" || msg.type === "wake_word") {
          setVoiceState("listening");
        } else if (msg.type === "thinking") {
          setVoiceState("thinking");
        } else if (msg.type === "speaking_start") {
          setVoiceState("speaking");
        } else if (msg.type === "speaking_stop" || msg.type === "response_done") {
          setVoiceState("idle");
        } else if (msg.type === "audio_state") {
          setAudio({
            muted: Boolean(msg.payload?.muted),
            volume: typeof msg.payload?.volume === "number" ? msg.payload.volume : 1.0,
          });
        } else if (msg.type === "mic_state") {
          setMic({ mic_muted: Boolean(msg.payload?.mic_muted) });
        } else if (msg.type === "session_updated") {
          const sid = sessionOf(msg);
          const title = msg.title || msg.payload?.title;
          const deleted = msg.payload?.deleted;
          if (sid && deleted) {
            const cur = useCharlieStore.getState().sessions;
            setSessions(cur.filter((s) => s.id !== sid));
            if (useCharlieStore.getState().currentSessionId === sid) {
              setCurrentSessionId("");
            }
          } else if (sid && title) {
            const cur = useCharlieStore.getState().sessions;
            setSessions(cur.map((s) => (s.id === sid ? { ...s, title } : s)));
          }
        } else if (msg.type === "audio_level") {
          const level = typeof msg.payload?.level === "number" ? msg.payload.level : 0;
          setAudioLevel(Math.max(0, Math.min(1, level)));
        } else if (msg.type === "log") {
          addLog(msg.payload?.line || "");
        } else if (msg.type === "alert") {
          addAlert({
            severity: msg.payload?.severity || "info",
            message: msg.payload?.message || "",
            timestamp: new Date().toLocaleTimeString(),
          });
        }
        
        // Spoken input (STT): the backend streams recognized speech as
        // "transcript" events. Surface the final utterance as a user bubble
        // in the active session so voice and chat stay in one thread.
        else if (msg.type === "transcript") {
          const eventSession = sessionOf(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          const spoken = (msg.payload?.text || "").trim();
          if (spoken) {
            addMessage({ role: "user", content: spoken });
          }
        }
        // Handle real-time token stream. Only render tokens for the active
        // session; the server also filters by subscription, but we guard
        // here too so a stray cross-session token can never bleed in.
        else if (msg.type === "token") {
          const eventSession = sessionOf(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          updateLastMessageContent(msg.payload?.text || "");
        }
      } catch (err) {
        console.error("Error parsing WS event packet:", err);
      }
    };
  }, [setConnected, setSystemStatus, setBlackboard, setVoiceState, setAudio, setMic, setAudioLevel, addLog, addAlert, addMessage, updateLastMessageContent, fetchMessages, setSessions, setCurrentSessionId]);
  useEffect(() => { connectWSRef.current = connectWS; });
  useEffect(() => { currentSessionIdRef.current = currentSessionId; }, [currentSessionId]);

  // Send text command packet
  const handleSendMessage = async (text: string) => {
    if (!currentSessionId) return;

    // Append optimistic user bubble
    addMessage({ role: "user", content: text });

    sendWS({ type: "chat", payload: { session_id: currentSessionId, text } });
    if (!(wsRef.current && wsRef.current.readyState === WebSocket.OPEN)) {
      // HTTP POST fallback if socket is down
      try {
        await fetch(`/api/sessions/${currentSessionId}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
      } catch (err) {
        console.error("HTTP chat command fallback failed:", err);
      }
    }
  };

  const handleStop = () => {
    setVoiceState("idle");
    setMessagesLoading(false);
    sendWS({ type: "stop" });
  };

  const handleTerminateAgent = (agentName: string) => {
    sendWS({ type: "agent_kill", payload: { name: agentName } });
  };

  // Export full chat history (real backend data)
  const handleExportHistory = useCallback(async () => {
    try {
      const res = await fetch("/api/history?limit=1000");
      if (!res.ok) return;
      const data = await res.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `charlie-history-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("History export failed:", err);
    }
  }, []);

  // Push speaker controls to the backend audio subsystem via WebSocket
  const sendAudioControl = useCallback((patch: { muted?: boolean; volume?: number }) => {
    sendWS({ type: "audio_control", payload: patch });
  }, [sendWS]);

  // Push microphone mute toggle to the backend voice engine via WebSocket.
  // The backend gates captured frames, so the assistant truly stops listening.
  const sendMicControl = useCallback((patch: { mic_muted: boolean }) => {
    sendWS({ type: "mic_control", payload: patch });
  }, [sendWS]);

  // Create new session
  const handleCreateSession = useCallback(async (title: string = "New Chat") => {
    try {
      const res = await fetch("/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      if (res.ok) {
        const data = await res.json();
        const updatedSessions = await fetchSessions();
        if (data.session_id) {
          setCurrentSessionId(data.session_id);
        } else if (updatedSessions.length > 0) {
          setCurrentSessionId(updatedSessions[0].id);
        }
      }
    } catch (err) {
      console.error("Error creating session:", err);
    }
  }, [fetchSessions, setCurrentSessionId]);

  // Rename session
  const handleRenameSession = async (id: string, title: string) => {
    try {
      const res = await fetch(`/api/sessions/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      if (res.ok) {
        await fetchSessions();
      }
    } catch (err) {
      console.error("Error renaming session:", err);
    }
  };

  // Delete session
  const handleDeleteSession = useCallback(async (id: string) => {
    try {
      const res = await fetch(`/api/sessions/${id}`, { method: "DELETE" });
      if (res.ok) {
        const updatedSessions = await fetchSessions();
        if (currentSessionId === id) {
          if (updatedSessions.length > 0) {
            const nextActive = updatedSessions.find((s) => s.id !== id)?.id || updatedSessions[0].id;
            setCurrentSessionId(nextActive);
          } else {
            setCurrentSessionId("");
          }
        }
      }
    } catch (err) {
      console.error("Error deleting session:", err);
    }
  }, [fetchSessions, currentSessionId, setCurrentSessionId]);

  // Initial load
  useEffect(() => {
    // Create a fresh session only if none already exist. Repeated mounts
    // (StrictMode double-invoke, HMR, reconnects) must not spawn a new
    // "New Chat" each time -- that churn is what caused duplicate bubbles.
    const bootstrap = async () => {
      const existing = await fetchSessions();
      if (existing.length === 0) {
        await handleCreateSession("New Chat");
        await fetchSessions();
      }
    };
    const init = async () => {
      await bootstrap();
      const audio = await fetchJson("/api/audio");
      if (audio) setAudio({ muted: Boolean((audio as { muted: boolean }).muted), volume: (audio as { volume: number }).volume ?? 1.0 });
      const mic = await fetchJson("/api/mic");
      if (mic && typeof (mic as { mic_muted: boolean }).mic_muted === "boolean") {
        setMic({ mic_muted: (mic as { mic_muted: boolean }).mic_muted });
      }
    };
    void init();
  }, [fetchSessions, handleCreateSession, setAudio, setMic, fetchJson]);

  // Sync messages when active session changes
  useEffect(() => {
    if (currentSessionId) {
      fetchMessages(currentSessionId);
      
      // Sync WebSocket focus
      sendWS({ type: "session_active", payload: { session_id: currentSessionId } });

      // HTTP POST active session update fallback
      fetch("/api/session/active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: currentSessionId }),
      }).catch((e) => console.error("HTTP active session focus sync failed:", e));
    }
  }, [currentSessionId, fetchMessages]);

  // Connect WebSocket loop
  useEffect(() => {
    connectWS();
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
    };
  }, [connectWS]);
  // Cleanup abort controller on unmount
  useEffect(() => {
    return () => {
      abortSessionsRef.current?.abort();
      abortMessagesRef.current?.abort();
    };
  }, []);

  return (
    <ErrorBoundary>
      <div className="h-screen w-screen flex flex-col overflow-hidden relative font-sans select-none text-[var(--color-text-primary)]">


        {/* Mobile Header */}
        <header className="md:hidden flex items-center justify-between px-6 py-3 border-b border-[var(--color-glass-border)] bg-[var(--color-glass-bg)] z-30">
          <h1 className="font-display font-semibold text-[var(--color-text-primary)]">Charlie</h1>
          <button
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="p-2 rounded-xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] text-[var(--color-text-secondary)] hover:text-white"
            aria-label="Toggle menu"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d={mobileMenuOpen ? "M6 18L18 6M6 6l12 12" : "M4 6h16M4 12h16m-7 6h7"} />
            </svg>
          </button>
        </header>

        <div className="flex-1 flex overflow-hidden z-10 p-4 pb-2 gap-4 relative">
          {/* Left: session rail */}
          <div className={`${mobileMenuOpen ? 'flex absolute inset-y-4 left-4 z-20 shadow-2xl' : 'hidden'} md:flex md:static h-full`}>
            <SessionRail
              collapsed={railCollapsed}
              onToggle={() => setRailCollapsed((v) => !v)}
              sessions={sessions}
              currentId={currentSessionId}
              onSelect={(id) => setCurrentSessionId(id)}
              onCreate={() => handleCreateSession("New Chat")}
              onRename={handleRenameSession}
              onDelete={handleDeleteSession}
              onExport={handleExportHistory}
            />
          </div>

          <main className="flex-1 min-w-0 flex flex-col h-full">
            <ChatView
              messages={messages}
              onSend={handleSendMessage}
              onStop={handleStop}
              loading={messagesLoading}
              voiceState={voiceState}
            />
          </main>

          {/* Right: insight rail (Swarm / Memory / MCP / Tasks) */}
          <div className="hidden xl:flex h-full">
            <InsightRail
              blackboard={blackboard}
              systemStatus={systemStatus}
              onTerminateAgent={handleTerminateAgent}
            />
          </div>
        </div>

        <VoiceDock
          state={voiceState}
          connected={connected}
          audio={audio}
          mic={mic}
          onAudioControl={sendAudioControl}
          onMicControl={sendMicControl}
        />
      </div>
    </ErrorBoundary>
  );
}
