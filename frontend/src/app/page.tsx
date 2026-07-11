"use client";

import { useEffect, useCallback, useRef, useState } from "react";
import { useCharlieStore } from "../store/useCharlieStore";
import { SessionRail } from "../components/SessionRail";
import { ChatView } from "../components/ChatView";
import { InsightRail } from "../components/InsightRail";
import { VoiceDock } from "../components/VoiceDock";
import { EventLog } from "../components/EventLog";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { MicMeter } from "../components/MicMeter";

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
  const appendToolActivity = useCharlieStore((s) => s.appendToolActivity);
  const clearToolActivity = useCharlieStore((s) => s.clearToolActivity);
  const toolActivity = useCharlieStore((s) => s.toolActivity);
  const launchId = useCharlieStore((s) => s.launchId);
  const setLaunchId = useCharlieStore((s) => s.setLaunchId);
  const sessionScope = useCharlieStore((s) => s.sessionScope);
  const setSessionScope = useCharlieStore((s) => s.setSessionScope);
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
  // Tracks sessions currently receiving a streamed WS reply. Used to suppress
  // the duplicate HTTP /chat fallback in handleSendMessage during streaming.
  const wsStreamingRef = useRef<Set<string>>(new Set());
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
    // Pass launch_id when the sidebar is scoped to "This Launch" so the
    // backend only returns sessions created during this process launch.
    const state = useCharlieStore.getState();
    const url =
      state.sessionScope === "this_launch" && state.launchId
        ? `/api/sessions?launch_id=${encodeURIComponent(state.launchId)}`
        : "/api/sessions";
    try {
      const res = await fetch(url, { signal });
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
    }
    return [];
  }, [setSessions, setCurrentSessionId, abortSessions]);

  // Fetch messages for active session
  const fetchMessages = useCallback(async (sid: string) => {
    if (!sid || fetchMessagesInFlight.current === sid) return;
    fetchMessagesInFlight.current = sid;
    // Capture the session this fetch was started for. If the active session
    // changes while the request is in flight, the resolved payload must NOT
    // overwrite the new session's thread.
    const requestedSid = sid;
    const signal = abortMessages();
    setMessagesLoading(true);
    try {
      const res = await fetch(`/api/sessions/${requestedSid}/messages`, { signal });
      if (res.ok) {
        const data = await res.json();
        if (currentSessionIdRef.current !== requestedSid) return;
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
    } finally {
      if (currentSessionIdRef.current !== requestedSid) return;
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
        // Re-send the subscription shortly after, guarded by a still-open
        // socket. This survives the ZMQ slow-joiner race where the first
        // session_active can arrive before the subscriber is wired up.
        setTimeout(() => {
          if (socket.readyState === WebSocket.OPEN && currentSessionIdRef.current) {
            socket.send(JSON.stringify({ type: "session_active", payload: { session_id: currentSessionIdRef.current } }));
          }
        }, 250);
      }
    };

    socket.onclose = () => {
      setConnected(false);
      wsRef.current = null;
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
          // A reply turn has finished: drop this session from the streaming set
          // so the HTTP fallback can run again, and reset per-reply tool rows.
          if (msg.type === "response_done") {
            const eventSession = sessionOf(msg);
            wsStreamingRef.current.delete(eventSession || currentSessionIdRef.current);
            clearToolActivity();
          }
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
        
        // Tool activity + thinking events streamed from the backend. These had
        // no WS handler before, so tool rows never appeared in the UI. Route
        // them to the tool-activity list, guarded by session isolation.
        else if (msg.type === "tool_call") {
          const eventSession = sessionOf(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          appendToolActivity({ kind: "tool_call", name: msg.payload?.name || "tool", text: msg.payload?.text || "", sessionId: eventSession });
        }
        else if (msg.type === "tool_result") {
          const eventSession = sessionOf(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          appendToolActivity({ kind: "tool_result", name: msg.payload?.name || "tool", text: msg.payload?.text || "", sessionId: eventSession });
        }
        else if (msg.type === "thinking_update") {
          const eventSession = sessionOf(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          appendToolActivity({ kind: "thinking_update", name: "thinking", text: msg.payload?.text || "", sessionId: eventSession });
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
          // Mark this session as actively streaming so the HTTP /chat fallback
          // in handleSendMessage is suppressed for the duration of the reply.
          wsStreamingRef.current.add(eventSession || currentSessionIdRef.current);
          updateLastMessageContent(msg.payload?.text || "");
        }
      } catch {
        // Malformed WS packet: ignore so the socket stays alive.
      }
    };
  }, [setConnected, setSystemStatus, setBlackboard, setVoiceState, setAudio, setMic, setAudioLevel, addLog, addAlert, addMessage, updateLastMessageContent, fetchMessages, setSessions, setCurrentSessionId]);
  useEffect(() => { connectWSRef.current = connectWS; });
  useEffect(() => { currentSessionIdRef.current = currentSessionId; }, [currentSessionId]);

  // Send text command packet
  const handleSendMessage = async (text: string) => {
    if (!currentSessionId) return;
    const sid = currentSessionId;

    // Append optimistic user bubble
    addMessage({ role: "user", content: text });

    sendWS({ type: "chat", payload: { session_id: sid, text } });
    // Only fall back to HTTP /chat when the socket is down AND we are not
    // already streaming a WS reply for this session (which would duplicate it).
    if (!(wsRef.current && wsRef.current.readyState === WebSocket.OPEN) && !wsStreamingRef.current.has(sid)) {
      // HTTP POST fallback if socket is down
      try {
        await fetch(`/api/sessions/${sid}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
      } catch {
        // Socket is the primary path; ignore HTTP fallback failure.
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
    } catch {
      // Export is best-effort; ignore failures.
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
    } catch {
      // Session creation failure leaves the UI as-is.
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
    } catch {
      // Rename failure leaves the local list unchanged.
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
    } catch {
      // Delete failure leaves the local list unchanged.
    }
  }, [fetchSessions, currentSessionId, setCurrentSessionId]);

  // Initial load
  const toggleSessionScope = useCallback(() => {
    const next = sessionScope === "this_launch" ? "all" : "this_launch";
    setSessionScope(next);
    void fetchSessions();
  }, [sessionScope, setSessionScope, fetchSessions]);

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
      // Fetch launch_id first so the sidebar "This Launch" filter is exercised.
      const status = await fetchJson("/api/status");
      if (status && typeof (status as { launch_id?: string }).launch_id === "string") {
        setLaunchId((status as { launch_id: string }).launch_id);
        setSessionScope("this_launch");
      }
      await bootstrap();
      const audio = await fetchJson("/api/audio");
      if (audio) setAudio({ muted: Boolean((audio as { muted: boolean }).muted), volume: (audio as { volume: number }).volume ?? 1.0 });
      const mic = await fetchJson("/api/mic");
      if (mic && typeof (mic as { mic_muted: boolean }).mic_muted === "boolean") {
        setMic({ mic_muted: (mic as { mic_muted: boolean }).mic_muted });
      }
    };
    void init();
  }, [fetchSessions, handleCreateSession, setAudio, setMic, fetchJson, setLaunchId, setSessionScope]);

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
      }).catch(() => {});
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
          <MicMeter />
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
              onScopeChange={toggleSessionScope}
            />
          </div>

          <main className="flex-1 min-w-0 flex flex-col h-full">
            <ChatView
              messages={messages}
              onSend={handleSendMessage}
              onStop={handleStop}
              loading={messagesLoading}
              voiceState={voiceState}
              toolActivity={toolActivity}
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

        <div className="shrink-0 px-1 mt-2">
          <EventLog />
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
