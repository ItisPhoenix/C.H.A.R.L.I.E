"use client";

import { useEffect, useCallback, useRef, useState } from "react";
import { useCharlieStore, rgba } from "../store/useCharlieStore";
import { SessionRail } from "../components/SessionRail";
import { ChatView } from "../components/ChatView";
import { InsightRail } from "../components/InsightRail";
import { VoiceDock } from "../components/VoiceDock";
import { EventLog } from "../components/EventLog";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { MicMeter } from "../components/MicMeter";
import { RecoveryDialog } from "../components/RecoveryDialog";
import { ToolApprovalDialog } from "../components/ToolApprovalDialog";

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
  const accentColor = useCharlieStore((s) => s.accentColor);
  const activeProposal = useCharlieStore((s) => s.activeProposal);
  const setActiveProposal = useCharlieStore((s) => s.setActiveProposal);
  const setDesktopControlEnabled = useCharlieStore((s) => s.setDesktopControlEnabled);

  const [railCollapsed, setRailCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [viewportWidth, setViewportWidth] = useState(typeof window !== "undefined" ? window.innerWidth : 1440);

  useEffect(() => {
    const handleResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const effectiveCollapsed = railCollapsed || viewportWidth < 860;

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

  // The backend's "active session" (which routes voice input) is one shared
  // value with no per-tab isolation -- any connected tab announcing itself
  // silently steals routing from whichever tab the user is actually looking
  // at, including a background tab reconnecting on its own after a network
  // blip. Only the visible tab may announce itself as active.
  const announceActiveSession = useCallback((sid: string) => {
    if (!sid || typeof document === "undefined" || document.visibilityState !== "visible") {
      return;
    }
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "session_active", payload: { session_id: sid } }));
    } else {
      fetch("/api/session/active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid }),
      }).catch(() => {
        console.warn("Failed to sync active session over HTTP fallback (network error)");
      });
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
        // Sort newest-first so most-recently-updated session floats to top
        const sorted = (data.sessions || []).sort((a: {updated_at?: string; created_at?: string}, b: {updated_at?: string; created_at?: string}) => {
          const ta = a.updated_at || a.created_at || "";
          const tb = b.updated_at || b.created_at || "";
          return tb.localeCompare(ta);
        });
        setSessions(sorted);
        // Only auto-focus the first session if none is active yet.
        if (sorted.length > 0 && !useCharlieStore.getState().currentSessionId) {
          setCurrentSessionId(sorted[0].id);
        }
        return sorted;
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
  const fetchMessagesRef = useRef(fetchMessages);
  useEffect(() => {
    fetchMessagesRef.current = fetchMessages;
  }, [fetchMessages]);
  const announceActiveSessionRef = useRef(announceActiveSession);
  useEffect(() => {
    announceActiveSessionRef.current = announceActiveSession;
  }, [announceActiveSession]);


  // Connect WebSocket
  const connectWS = useCallback(() => {
    if (wsRef.current) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws`;
    const socket = new WebSocket(wsUrl);
    wsRef.current = socket;

    socket.onopen = () => {
      useCharlieStore.getState().setConnected(true);
      reconnectAttemptsRef.current = 0;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      // Sync active session on (re)connect, then pull the latest transcript
      // so the UI self-heals after a dropout without a manual page refresh.
      // announceActiveSession no-ops if this tab isn't visible -- a
      // background tab reconnecting on its own must not steal routing.
      if (currentSessionIdRef.current) {
        announceActiveSessionRef.current(currentSessionIdRef.current);
        fetchMessagesRef.current(currentSessionIdRef.current);
        // Re-send the subscription shortly after. This survives the ZMQ
        // slow-joiner race where the first session_active can arrive before
        // the subscriber is wired up.
        setTimeout(() => {
          announceActiveSessionRef.current(currentSessionIdRef.current);
        }, 250);
      }
    };

    socket.onclose = () => {
      useCharlieStore.getState().setConnected(false);
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
        const store = useCharlieStore.getState();
        
        // Handle telemetry and status updates
        if (msg.type === "system_status") {
          store.setSystemStatus(msg.payload);
        } else if (msg.type === "vad_start" || msg.type === "wake_word") {
          store.setVoiceState("listening");
          store.setListeningTrigger(msg.type === "wake_word" ? "wake_word" : "vad");
        } else if (msg.type === "thinking") {
          store.setVoiceState("thinking");
          store.setListeningTrigger(null);
        } else if (msg.type === "speaking_start") {
          store.setVoiceState("speaking");
          store.setListeningTrigger(null);
        } else if (msg.type === "speaking_stop" || msg.type === "response_done") {
          store.setVoiceState("idle");
          store.setListeningTrigger(null);
          // A reply turn has finished: drop this session from the streaming set
          // so the HTTP fallback can run again, and reset per-reply tool rows.
          if (msg.type === "response_done") {
            const eventSession = sessionOf(msg);
            wsStreamingRef.current.delete(eventSession || currentSessionIdRef.current);
            store.clearToolActivity();
          }
        } else if (msg.type === "audio_state") {
          store.setAudio({
            muted: Boolean(msg.payload?.muted),
            volume: typeof msg.payload?.volume === "number" ? msg.payload.volume : 1.0,
          });
        } else if (msg.type === "mic_state") {
          store.setMic({ mic_muted: Boolean(msg.payload?.mic_muted) });
        } else if (msg.type === "session_updated") {
          const sid = sessionOf(msg);
          const title = msg.title || msg.payload?.title;
          const deleted = msg.payload?.deleted;
          if (sid && deleted) {
            const cur = store.sessions;
            store.setSessions(cur.filter((s) => s.id !== sid));
            if (store.currentSessionId === sid) {
              store.setCurrentSessionId("");
            }
          } else if (sid && title) {
            const cur = store.sessions;
            store.setSessions(cur.map((s) => (s.id === sid ? { ...s, title } : s)));
          }
        } else if (msg.type === "audio_level") {
          const level = typeof msg.payload?.level === "number" ? msg.payload.level : 0;
          store.setAudioLevel(Math.max(0, Math.min(1, level)));
        } else if (msg.type === "log") {
          store.addLog(msg.payload?.line || "");
        } else if (msg.type === "alert") {
          store.addAlert({
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
          store.appendToolActivity({ kind: "tool_call", name: msg.payload?.name || "tool", text: msg.payload?.text || "", sessionId: eventSession });
        }
        else if (msg.type === "tool_result") {
          const eventSession = sessionOf(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          store.appendToolActivity({ kind: "tool_result", name: msg.payload?.name || "tool", text: msg.payload?.text || "", sessionId: eventSession });
        }
        else if (msg.type === "thinking_update") {
          const eventSession = sessionOf(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          store.appendToolActivity({ kind: "thinking_update", name: "thinking", text: msg.payload?.text || "", sessionId: eventSession });
        }

        // Spoken input (STT): the backend streams recognized speech as
        // "transcript" events. Surface the final utterance as a user bubble
        // in the active session so voice and chat stay in one thread.
        else if (msg.type === "transcript") {
          const eventSession = sessionOf(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          const spoken = (msg.payload?.text || "").trim();
          if (spoken) {
            store.addMessage({ role: "user", content: spoken });
          }
        }
        else if (msg.type === "desktop_frame") {
          const eventSession = sessionOf(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          store.setLatestDesktopFrame({
            sessionId: eventSession || currentSessionIdRef.current,
            imageB64: msg.payload?.image_b64 || "",
            marks: msg.payload?.marks || [],
            receivedAt: Date.now(),
          });
        }
        else if (msg.type === "recovery_proposal") {
          const eventSession = sessionOf(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          store.setActiveProposal(msg.payload);
        }
        else if (msg.type === "tool_approval_request") {
          const eventSession = sessionOf(msg);
          if (eventSession && eventSession !== currentSessionIdRef.current) return;
          store.setActiveToolApproval(msg.payload);
        }
        else if (msg.type === "background_task") {
          store.setBackgroundTask(msg.payload);
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
          store.updateLastMessageContent(msg.payload?.text || "");
        }
      } catch {
        // Malformed WS packet: ignore so the socket stays alive.
      }
    };
  }, []);
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
        const res = await fetch(`/api/sessions/${sid}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        if (!res.ok) {
          addMessage({ role: "assistant", content: "Message failed to send (connection issue). Please try again." });
        }
      } catch {
        addMessage({ role: "assistant", content: "Message failed to send (connection issue). Please try again." });
      }
    }
  };

  const handleStop = () => {
    setVoiceState("idle");
    setMessagesLoading(false);
    sendWS({ type: "stop" });
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

  const sendBackgroundTaskStart = (text: string) => {
    sendWS({ type: "background_task_start", payload: { text } });
  };

  const sendBackgroundTaskCancel = (taskId: string) => {
    sendWS({ type: "background_task_cancel", payload: { task_id: taskId } });
  };

  const sendBackgroundTaskApprove = (taskId: string) => {
    sendWS({ type: "background_task_approve", payload: { task_id: taskId } });
  };

  const sendBackgroundTaskReject = (taskId: string) => {
    sendWS({ type: "background_task_reject", payload: { task_id: taskId } });
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
    const currentAudio = useCharlieStore.getState().audio;
    setAudio({
      ...currentAudio,
      ...patch,
    });
    sendWS({ type: "audio_control", payload: patch });
  }, [sendWS, setAudio]);

  // Push microphone mute toggle to the backend voice engine via WebSocket.
  // The backend gates captured frames, so the assistant truly stops listening.
  const sendMicControl = useCallback((patch: { mic_muted: boolean }) => {
    setMic({ mic_muted: patch.mic_muted });
    sendWS({ type: "mic_control", payload: patch });
  }, [sendWS, setMic]);

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
          // Exclude the just-deleted id when picking a fallback
          const remaining = updatedSessions.filter((s) => s.id !== id);
          setCurrentSessionId(remaining.length > 0 ? remaining[0].id : "");
        }
      }
    } catch {
      // Delete failure leaves the local list unchanged.
    }
  }, [fetchSessions, currentSessionId, setCurrentSessionId]);

  // Scope toggle: caller passes target scope explicitly so fetchSessions
  // always reads the committed value rather than the stale closure.
  const toggleSessionScope = useCallback((target: "all" | "this_launch") => {
    if (target === sessionScope) return;
    setSessionScope(target);
    // fetchSessions reads from getState() internally, so we need a microtask
    // gap for Zustand to flush the new value before the URL is built.
    setTimeout(() => void fetchSessions(), 0);
  }, [sessionScope, setSessionScope, fetchSessions]);

  useEffect(() => {
    const init = async () => {
      // 1. Fetch launch_id first — needed for session scoping.
      const status = await fetchJson("/api/status");
      const lid =
        status && typeof (status as { launch_id?: string }).launch_id === "string"
          ? (status as { launch_id: string }).launch_id
          : "";
      if (lid) {
        setLaunchId(lid);
      }
      setDesktopControlEnabled(
        Boolean((status as { desktop_control_enabled?: boolean } | null)?.desktop_control_enabled)
      );
      // 2. Always default to This Launch scope.
      setSessionScope("this_launch");

      // 3. Boot session: create exactly ONE fresh session per launch_id, not
      //    per page load. sessionStorage is tab-scoped and survives a
      //    refresh, so re-mounting the page during the SAME Charlie launch
      //    reuses that session instead of throwing away the active
      //    conversation. A genuinely new launch_id (real Charlie restart)
      //    still gets a brand-new blank thread.
      //    Falls back to a fixed key when there's no launch_id (e.g.
      //    `run.py --web-only`, which never sets one) -- previously an empty
      //    `lid` made this whole reuse check a no-op, so a plain page
      //    refresh created a brand-new blank "New Chat" session every time.
      const bootKey = `charlie_boot_session::${lid || "no-launch"}`;
      const storedBootSid =
        typeof window !== "undefined" ? window.sessionStorage.getItem(bootKey) : null;
      const existingSessions = await fetchSessions();
      const bootSessionStillValid = Boolean(
        storedBootSid && existingSessions.some((s) => s.id === storedBootSid)
      );
      if (bootSessionStillValid && storedBootSid) {
        setCurrentSessionId(storedBootSid);
      } else {
        await handleCreateSession("New Chat"); // also refreshes the session list
        if (typeof window !== "undefined") {
          const created = useCharlieStore.getState().currentSessionId;
          if (created) window.sessionStorage.setItem(bootKey, created);
        }
      }

      // 4. Restore audio/mic state.
      const audio = await fetchJson("/api/audio");
      if (audio)
        setAudio({
          muted: Boolean((audio as { muted: boolean }).muted),
          volume: (audio as { volume: number }).volume ?? 1.0,
        });
      const mic = await fetchJson("/api/mic");
      if (mic && typeof (mic as { mic_muted: boolean }).mic_muted === "boolean") {
        setMic({ mic_muted: (mic as { mic_muted: boolean }).mic_muted });
      }
    };
    void init();
  }, [fetchSessions, handleCreateSession, setAudio, setMic, fetchJson, setLaunchId, setSessionScope, setDesktopControlEnabled]);

  // Sync messages when active session changes. announceActiveSession
  // no-ops (including its own HTTP fallback) if this tab isn't visible.
  useEffect(() => {
    if (currentSessionId) {
      fetchMessages(currentSessionId);
      announceActiveSession(currentSessionId);
    }
  }, [currentSessionId, fetchMessages, announceActiveSession]);

  // Reclaim active-session routing when this tab regains visibility/focus --
  // otherwise a background tab that becomes the one the user is actually
  // looking at never re-announces itself, and voice stays routed to
  // whatever tab last had focus.
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

  const canvasBg = `radial-gradient(1200px 700px at 12% -8%, ${rgba(accentColor, 0.12)}, transparent 60%), radial-gradient(1000px 600px at 105% 10%, ${rgba(accentColor, 0.06)}, transparent 55%), #000000`;

  return (
    <ErrorBoundary>
      <div 
        style={{ background: canvasBg }}
        className="h-screen w-screen flex flex-col overflow-hidden relative font-sans select-none text-[var(--color-text-primary)]"
      >
        {/* Glow Blobs */}
        <div style={{
          position: "absolute",
          top: "-160px",
          left: "-120px",
          width: "520px",
          height: "520px",
          borderRadius: "50%",
          background: `radial-gradient(circle, ${rgba(accentColor, 0.16)}, transparent 70%)`,
          filter: "blur(10px)",
          animation: "glowDrift 22s ease-in-out infinite",
          pointerEvents: "none",
        }} />
        <div style={{
          position: "absolute",
          bottom: "-200px",
          right: "-160px",
          width: "600px",
          height: "600px",
          borderRadius: "50%",
          background: `radial-gradient(circle, ${rgba(accentColor, 0.1)}, transparent 70%)`,
          filter: "blur(10px)",
          animation: "glowDrift 26s ease-in-out infinite reverse",
          pointerEvents: "none",
        }} />

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
              collapsed={effectiveCollapsed}
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

          {/* Right: insight rail (Memory / Extensions / Desktop) */}
          <div className="hidden xl:flex h-full">
            <InsightRail
              systemStatus={systemStatus}
              onStartBackgroundTask={sendBackgroundTaskStart}
              onCancelBackgroundTask={sendBackgroundTaskCancel}
              onApproveBackgroundTask={sendBackgroundTaskApprove}
              onRejectBackgroundTask={sendBackgroundTaskReject}
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

        <RecoveryDialog
          onApprove={handleApproveRecovery}
          onReject={handleRejectRecovery}
        />
        <ToolApprovalDialog
          onApprove={handleApproveToolCall}
          onReject={handleRejectToolCall}
        />
      </div>
    </ErrorBoundary>
  );
}
