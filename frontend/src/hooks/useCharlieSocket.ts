"use client";

import { useCallback, useEffect, useRef } from "react";
import { useCharlieStore, type Session } from "../store/useCharlieStore";

interface WSMessage {
  type: string;
  session_id?: string;
  payload?: Record<string, unknown> & { session_id?: string };
}

function getSessionId(msg: WSMessage): string | undefined {
  return msg.session_id || msg.payload?.session_id || undefined;
}

interface UseCharlieSocketParams {
  currentSessionId: string;
  fetchSessions: () => Promise<Session[]>;
  fetchMessages: (id: string) => Promise<void>;
  announceActiveSession: (id: string) => Promise<void> | void;
}

interface UseCharlieSocketResult {
  sendWS: (data: { type: string; payload?: Record<string, unknown> }) => void;
  isSocketOpen: () => boolean;
  isStreaming: (sessionId: string) => boolean;
}

/** WebSocket client: connect/backoff/reconnect + onmessage dispatcher. Ref-based params keep connectWS a single mount effect, not torn down on every prop change. */
export function useCharlieSocket(params: UseCharlieSocketParams): UseCharlieSocketResult {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const wsStreamingRef = useRef<Set<string>>(new Set());
  const connectWSRef = useRef<() => void>(() => {});
  const currentSessionIdRef = useRef("");

  const fetchSessionsRef = useRef(params.fetchSessions);
  const fetchMessagesRef = useRef(params.fetchMessages);
  const announceActiveSessionRef = useRef(params.announceActiveSession);

  useEffect(() => { fetchSessionsRef.current = params.fetchSessions; }, [params.fetchSessions]);
  useEffect(() => { fetchMessagesRef.current = params.fetchMessages; }, [params.fetchMessages]);
  useEffect(() => { announceActiveSessionRef.current = params.announceActiveSession; }, [params.announceActiveSession]);
  useEffect(() => { currentSessionIdRef.current = params.currentSessionId; }, [params.currentSessionId]);

  const setConnected = useCharlieStore((s) => s.setConnected);
  const setSystemStatus = useCharlieStore((s) => s.setSystemStatus);
  const setVoiceState = useCharlieStore((s) => s.setVoiceState);
  const setListeningTrigger = useCharlieStore((s) => s.setListeningTrigger);
  const setAudio = useCharlieStore((s) => s.setAudio);
  const setMic = useCharlieStore((s) => s.setMic);
  const setQueue = useCharlieStore((s) => s.setQueue);
  const setAudioLevel = useCharlieStore((s) => s.setAudioLevel);
  const appendToolActivity = useCharlieStore((s) => s.appendToolActivity);
  const addMessage = useCharlieStore((s) => s.addMessage);
  const setSessions = useCharlieStore((s) => s.setSessions);
  const setCurrentSessionId = useCharlieStore((s) => s.setCurrentSessionId);

  const sendWS = useCallback((data: { type: string; payload?: Record<string, unknown> }) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  const isSocketOpen = useCallback(() => {
    return Boolean(wsRef.current && wsRef.current.readyState === WebSocket.OPEN);
  }, []);

  const isStreaming = useCallback((sessionId: string) => {
    return wsStreamingRef.current.has(sessionId);
  }, []);

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
          store.setCurrentSpeechChunk(msg.payload?.text || "");
        } else if (msg.type === "speaking_stop" || msg.type === "response_done") {
          setVoiceState("idle");
          setListeningTrigger(null);
          if (msg.type === "response_done") {
            store.setCurrentSpeechChunk(null);
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

  return { sendWS, isSocketOpen, isStreaming };
}
