"use client";

import { useEffect, useCallback, useRef, useState } from "react";
import { useCharlieStore } from "../store/useCharlieStore";
import { SessionRail } from "../components/SessionRail";
import { ChatView } from "../components/ChatView";
import { InsightRail } from "../components/InsightRail";
import { VoiceDock } from "../components/VoiceDock";
import { ErrorBoundary } from "../components/ErrorBoundary";

export default function Page() {
  const {
    connected,
    setConnected,
    systemStatus,
    setSystemStatus,
    sessions,
    setSessions,
    currentSessionId,
    setCurrentSessionId,
    messages,
    setMessages,
    messagesLoading,
    setMessagesLoading,
    logs,
    alerts,
    addLog,
    addAlert,
    blackboard,
    setBlackboard,
    voiceState,
    setVoiceState,
    audio,
    setAudio,
    mic,
    setMic,
    setAudioLevel,
    updateLastMessageContent,
    addMessage,
  } = useCharlieStore();

  const [railCollapsed, setRailCollapsed] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const connectWSRef = useRef<(() => void) | null>(null);
  const currentSessionIdRef = useRef<string>("");
  const abortFetch = useCallback(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    return controller.signal;
  }, []);

  // Fetch all sessions
  const fetchSessions = useCallback(async (): Promise<Array<{id: string}>> => {
    const signal = abortFetch();
    try {
      const res = await fetch("/api/sessions", { signal });
      if (res.ok) {
        const data = await res.json();
        setSessions(data.sessions || []);
        if (data.sessions && data.sessions.length > 0) {
          setCurrentSessionId(data.sessions[0].id);
        }
        return data.sessions || [];
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return [];
      console.error("Error fetching sessions:", err);
    }
    return [];
  }, [setSessions, setCurrentSessionId, abortFetch]);

  // Fetch messages for active session
  const fetchMessages = useCallback(async (sid: string) => {
    const signal = abortFetch();
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
      setMessagesLoading(false);
    }
  }, [setMessages, setMessagesLoading, abortFetch]);


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
      reconnectTimeoutRef.current = setTimeout(() => connectWSRef.current?.(), 3000);
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
          // Renamed from another surface: merge the new title into the rail.
          const sid = msg.session_id || msg.payload?.session_id;
          const title = msg.title || msg.payload?.title;
          if (sid && title) {
            setSessions(
              sessions.map((s) => (s.id === sid ? { ...s, title } : s))
            );
          }
        } else if (msg.type === "session_update") {
          const payload = msg.payload || {};
          if (payload.deleted) {
            const sid = msg.session_id || payload.session_id;
            const remaining = sessions.filter((s) => s.id !== sid);
            setSessions(remaining);
            if (currentSessionId === sid) {
              setCurrentSessionId(remaining[0]?.id ?? "");
            }
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
          const spoken = (msg.payload?.text || "").trim();
          if (spoken) {
            addMessage({ role: "user", content: spoken });
            addMessage({ role: "assistant", content: "" });
          }
        }
        // Handle real-time token stream. Render into the active session
        // regardless of the payload session_id so a reply is never dropped
        // due to a transient session-id mismatch. Voice and chat both stream
        // into the same active session, keeping them synced.
        else if (msg.type === "token") {
          updateLastMessageContent(msg.payload?.text || "");
        } else if (msg.type === "response_done") {
          // Force reload to get final transcript alignment
          fetchMessages(currentSessionIdRef.current);
        }
      } catch (err) {
        console.error("Error parsing WS event packet:", err);
      }
    };
  }, [setConnected, setSystemStatus, setBlackboard, setVoiceState, setAudio, setMic, setAudioLevel, addLog, addAlert, addMessage, updateLastMessageContent, fetchMessages]);
  useEffect(() => { connectWSRef.current = connectWS; });
  useEffect(() => { currentSessionIdRef.current = currentSessionId; }, [currentSessionId]);

  // Send text command packet
  const handleSendMessage = async (text: string) => {
    if (!currentSessionId) return;

    // Append optimistic user bubble
    addMessage({ role: "user", content: text });

    // Append assistant placeholder for tokens
    addMessage({ role: "assistant", content: "" });

    // Send packet via WebSocket
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: "chat",
          payload: { session_id: currentSessionId, text },
        })
      );
    } else {
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
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({ type: "audio_control", payload: patch })
      );
    }
  }, []);

  // Push microphone mute toggle to the backend voice engine via WebSocket.
  // The backend gates captured frames, so the assistant truly stops listening.
  const sendMicControl = useCallback((patch: { mic_muted: boolean }) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({ type: "mic_control", payload: patch })
      );
    }
  }, []);

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
    // Every dashboard launch starts a brand-new conversation. We create a
    // fresh session (rather than reusing the first existing one) so a new
    // start never inherits a prior chat's history.
    handleCreateSession("New Chat").then(() => fetchSessions());
    fetch("/api/audio")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d) setAudio({ muted: Boolean(d.muted), volume: d.volume ?? 1.0 });
      })
      .catch(() => {});
    fetch("/api/mic")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d && typeof d.mic_muted === "boolean") setMic({ mic_muted: d.mic_muted });
      })
      .catch(() => {});
  }, [fetchSessions, handleCreateSession, setAudio, setMic]);

  // Sync messages when active session changes
  useEffect(() => {
    if (currentSessionId) {
      fetchMessages(currentSessionId);
      
      // Sync WebSocket focus
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(
          JSON.stringify({
            type: "session_active",
            payload: { session_id: currentSessionId },
          })
        );
      }
      
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
      abortRef.current?.abort();
    };
  }, []);

  return (
    <ErrorBoundary>
      <div className="h-screen w-screen flex flex-col overflow-hidden relative font-sans select-none text-[var(--color-text-primary)]">
        {/* Living aurora backdrop */}
        <div className="aurora" aria-hidden="true" />

        <div className="flex-1 flex overflow-hidden z-10 p-4 pb-2 gap-4">
          {/* Left: session rail */}
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

          {/* Center: chat hero */}
          <main className="flex-1 min-w-0 flex flex-col">
            <ChatView
              messages={messages}
              onSend={handleSendMessage}
              loading={messagesLoading}
            />
          </main>

          {/* Right: insight rail (Swarm / Memory / MCP / Tasks) */}
          <InsightRail blackboard={blackboard} systemStatus={systemStatus} />
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
