import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import { createInitialRuntimeState, runtimeReducer, type RuntimeAction } from "./reducer";
import { isWidgetVisible } from "./widgets";
import type { MediaAction, RuntimeConnection, RuntimeActions, RuntimeEvent, RuntimeMediaSnapshot } from "./types";
import { adaptEvent, createLocalEvent } from "./bridge";

interface SocketLike {
  readonly readyState: number;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  send(data: string): void;
  close(): void;
}

export interface RuntimeClientOptions {
  url?: string;
  apiBaseUrl?: string;
  reconnect?: boolean;
  onEvent: (event: RuntimeEvent) => void;
  onStatus?: (status: RuntimeConnection) => void;
  onSessionId?: (sessionId: string | null) => void;
  fetchImpl?: typeof fetch;
  socketFactory?: (url: string) => SocketLike;
}

const SESSION_STORAGE_KEY = "charlie.runtime.session";

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;

const createId = (prefix: string) => {
  const uuid = globalThis.crypto?.randomUUID?.();
  return `${prefix}-${uuid ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
};

export function defaultRuntimeApiBaseUrl() {
  const configured = import.meta.env.VITE_CHARLIE_URL;
  if (typeof configured === "string" && configured.trim()) return configured.replace(/\/$/, "");
  if (typeof location !== "undefined" && location.port === "8000") return location.origin;
  const protocol = typeof location !== "undefined" && location.protocol === "https:" ? "https:" : "http:";
  const host = typeof location !== "undefined" && location.hostname ? location.hostname : "127.0.0.1";
  return `${protocol}//${host}:8000`;
}

export function runtimeWebSocketUrl(apiBaseUrl = defaultRuntimeApiBaseUrl()) {
  const url = new URL("/ws", apiBaseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function readStoredSession() {
  try {
    return sessionStorage.getItem(SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeSession(sessionId: string | null) {
  try {
    if (sessionId) sessionStorage.setItem(SESSION_STORAGE_KEY, sessionId);
    else sessionStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    // Private browsing and embedded hosts may disable sessionStorage.
  }
}

export class RuntimeClient {
  private readonly options: RuntimeClientOptions;
  private socket: SocketLike | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private attempt = 0;
  private reconnectCount = 0;
  private manuallyClosed = true;
  private sessionId: string | null = null;
  private readonly approvalRequestsInFlight = new Set<string>();

  constructor(options: RuntimeClientOptions) {
    this.options = options;
    this.sessionId = readStoredSession();
  }

  get currentSessionId() {
    return this.sessionId;
  }

  connect() {
    this.manuallyClosed = false;
    if (this.socket && this.socket.readyState <= 1) return;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const attempt = ++this.attempt;
    this.options.onStatus?.("connecting");
    void this.resolveSession().finally(() => {
      if (this.manuallyClosed || attempt !== this.attempt) return;
      try {
        const url = this.options.url ?? runtimeWebSocketUrl(this.options.apiBaseUrl);
        const socket = (this.options.socketFactory ?? (value => new WebSocket(value)))(url);
        this.socket = socket;
        socket.onopen = event => this.handleOpen(socket, event);
        socket.onmessage = event => this.handleMessage(event);
        socket.onerror = event => this.handleError(socket, event);
        socket.onclose = event => this.handleClose(socket, event);
      } catch {
        this.handleUnavailable();
      }
    });
  }

  close() {
    this.manuallyClosed = true;
    this.attempt += 1;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const socket = this.socket;
    this.socket = null;
    this.approvalRequestsInFlight.clear();
    try { socket?.close(); } catch { /* already closed */ }
    this.options.onStatus?.("disconnected");
  }

  send(type: string, payload: Record<string, unknown> = {}) {
    const socket = this.socket;
    if (!socket || socket.readyState !== 1) return false;
    try {
      socket.send(JSON.stringify({ type, payload }));
      return true;
    } catch {
      return false;
    }
  }

  sendChat(text: string) {
    const message = text.trim();
    if (!message || !this.sessionId) return false;
    const payload = {
      session_id: this.sessionId,
      text: message,
      request_id: createId("chat"),
    };
    const sent = this.send("chat", payload);
    if (sent) {
      const optimisticEvent = createLocalEvent("chat", payload, { session_id: this.sessionId });
      if (optimisticEvent) this.options.onEvent(optimisticEvent);
    }
    return sent;
  }

  sendToolApproval(requestId: string, approved: boolean) {
    const id = requestId.trim();
    if (!id || this.approvalRequestsInFlight.has(id)) return false;
    this.approvalRequestsInFlight.add(id);
    const sent = this.send(approved ? "tool_approve" : "tool_reject", { request_id: id });
    if (!sent) this.approvalRequestsInFlight.delete(id);
    return sent;
  }

  dismissPresentation(id: string) {
    return this.send("presentation_command", { action: "dismiss_widget", id });
  }

  openConversation() {
    return this.send("presentation_command", { action: "open_conversation" });
  }

  focusTask(taskId: string) {
    const id = taskId.trim();
    return id ? this.send("presentation_command", { action: "focus_task", task_id: id }) : false;
  }

  async fetchMediaSnapshot(): Promise<RuntimeMediaSnapshot> {
    const fetchImpl = this.options.fetchImpl ?? globalThis.fetch;
    if (!fetchImpl) throw new Error("Fetch is unavailable.");
    const base = this.options.apiBaseUrl ?? defaultRuntimeApiBaseUrl();
    const response = await fetchImpl(`${base}/api/media`, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`Media snapshot request failed (${response.status}).`);
    const data = asRecord(await response.json());
    if (!data || typeof data.available !== "boolean") throw new Error("Media snapshot was invalid.");
    return data as RuntimeMediaSnapshot;
  }

  async controlMedia(action: MediaAction, percent?: number) {
    const fetchImpl = this.options.fetchImpl ?? globalThis.fetch;
    if (!fetchImpl) return false;
    const base = this.options.apiBaseUrl ?? defaultRuntimeApiBaseUrl();
    const payload: Record<string, unknown> = { action, request_id: createId("media") };
    if (action === "set_volume") payload.percent = percent;
    try {
      const response = await fetchImpl(`${base}/api/media/control`, {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  setMicMuted(micMuted: boolean) {
    return this.send("mic_control", { mic_muted: micMuted });
  }

  ptt(action: "start" | "stop" | "cancel") {
    return this.send(`ptt_${action}`);
  }

  private async resolveSession() {
    const fetchImpl = this.options.fetchImpl ?? globalThis.fetch;
    if (!fetchImpl) return this.sessionId;
    try {
      const base = this.options.apiBaseUrl ?? defaultRuntimeApiBaseUrl();
      const response = await fetchImpl(`${base}/api/session/active`, { headers: { Accept: "application/json" } });
      if (!response.ok) {
        this.sessionId = null;
        storeSession(null);
        this.options.onSessionId?.(null);
        return null;
      }
      const data = asRecord(await response.json());
      let sessionId = typeof data?.session_id === "string" && data.session_id.trim()
        ? data.session_id.trim() : null;
      if (!sessionId && this.sessionId) {
        const activateResponse = await fetchImpl(`${base}/api/session/active`, {
          method: "POST",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: this.sessionId,
            request_id: createId("session-active"),
          }),
        });
        if (activateResponse.ok) {
          const activated = asRecord(await activateResponse.json());
          const result = asRecord(activated?.result);
          const activeSessionId = result?.active_session_id;
          if (activated?.status === "completed" && typeof activeSessionId === "string" && activeSessionId.trim()) {
            sessionId = activeSessionId.trim();
          }
        }
      }
      if (!sessionId) {
        const requestedSessionId = createId("web-session");
        const createResponse = await fetchImpl(`${base}/api/sessions`, {
          method: "POST",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: requestedSessionId,
            title: "New Chat",
            request_id: createId("session-create"),
          }),
        });
        if (createResponse.ok) {
          const created = asRecord(await createResponse.json());
          const result = asRecord(created?.result);
          const session = asRecord(result?.session);
          const createdSessionId = session?.session_id;
          if (created?.status === "completed" && typeof createdSessionId === "string" && createdSessionId.trim()) {
            sessionId = createdSessionId.trim();
          }
        }
      }
      this.sessionId = sessionId;
      storeSession(sessionId);
      this.options.onSessionId?.(sessionId);
      return sessionId;
    } catch {
      this.sessionId = null;
      storeSession(null);
      this.options.onSessionId?.(null);
      return null;
    }
  }

  private handleOpen(socket: SocketLike, event: Event) {
    if (socket !== this.socket) return;
    this.reconnectCount = 0;
    if (!this.sessionId) {
      this.options.onStatus?.("degraded");
      try { socket.close(); } catch { /* close event will schedule retry */ }
      return;
    }
    this.options.onStatus?.("connected");
    this.send("session_active", {
      session_id: this.sessionId,
      request_id: createId("session"),
    });
    void event;
  }

  private handleMessage(event: MessageEvent) {
    if (typeof event.data !== "string") return;
    let parsed: unknown;
    try { parsed = JSON.parse(event.data); } catch { return; }
    const message = adaptEvent(parsed);
    if (!message) return;
    if (message.type === "tool_approval_resolved") {
      const requestId = typeof message.payload.request_id === "string" ? message.payload.request_id : "";
      if (requestId) this.approvalRequestsInFlight.delete(requestId);
    }
    this.options.onEvent(message);
  }

  private handleError(socket: SocketLike, event: Event) {
    if (socket !== this.socket) return;
    this.options.onStatus?.("degraded");
    void event;
    try { socket.close(); } catch { /* close event will schedule retry */ }
  }

  private handleClose(socket: SocketLike, event: CloseEvent) {
    if (socket !== this.socket) return;
    this.socket = null;
    if (this.manuallyClosed || this.options.reconnect === false) {
      this.options.onStatus?.("disconnected");
      return;
    }
    this.options.onStatus?.("reconnecting");
    const delay = Math.min(8000, 500 * 2 ** Math.min(this.reconnectCount, 4));
    this.reconnectCount += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
    void event;
  }

  private handleUnavailable() {
    this.socket = null;
    if (this.manuallyClosed || this.options.reconnect === false) {
      this.options.onStatus?.("disconnected");
      return;
    }
    this.options.onStatus?.("reconnecting");
    const delay = Math.min(8000, 500 * 2 ** Math.min(this.reconnectCount, 4));
    this.reconnectCount += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }
}

export function useRuntime() {
  const [runtime, dispatch] = useReducer(runtimeReducer, undefined, createInitialRuntimeState);
  const clientRef = useRef<RuntimeClient | null>(null);
  if (!clientRef.current) {
    clientRef.current = new RuntimeClient({
      onEvent: event => dispatch({ type: "event", event }),
      onStatus: status => dispatch({ type: "connection", status }),
      onSessionId: sessionId => dispatch({ type: "session", sessionId }),
    });
  }
  const client = clientRef.current;
  const mediaLoadSource = runtime.widgets.media?.sourceId ?? null;
  const mediaLoadSourceRef = useRef<string | null>(null);

  useEffect(() => {
    client.connect();
    return () => client.close();
  }, [client]);

  const loadMedia = useCallback(async () => {
    dispatch({ type: "media_loading" });
    try {
      const snapshot = await client.fetchMediaSnapshot();
      dispatch({ type: "media_snapshot", snapshot });
      return snapshot.available;
    } catch (error) {
      dispatch({ type: "media_error", message: error instanceof Error ? error.message : "Media snapshot failed." });
      return false;
    }
  }, [client]);

  useEffect(() => {
    const mediaWidget = runtime.widgets.media;
    if (!mediaWidget || !isWidgetVisible(mediaWidget.lifecycle)) {
      mediaLoadSourceRef.current = null;
      return;
    }
    if (mediaLoadSourceRef.current === mediaLoadSource) return;
    mediaLoadSourceRef.current = mediaLoadSource;
    void loadMedia();
  }, [loadMedia, mediaLoadSource, runtime.widgets.media?.lifecycle]);

  const mediaControl = useCallback(async (action: MediaAction) => {
    dispatch({ type: "media_loading" });
    const sent = await client.controlMedia(action);
    if (!sent) {
      dispatch({ type: "media_error", message: "Media action was not accepted." });
      return false;
    }
    return loadMedia();
  }, [client, loadMedia]);

  const actions = useMemo<RuntimeActions>(() => ({
    approve: requestId => {
      if (runtime.inFlightApprovals[requestId]) return false;
      dispatch({ type: "approval_command", requestId });
      const sent = client.sendToolApproval(requestId, true);
      if (!sent) dispatch({ type: "command_error", message: "Runtime link unavailable; approval was not sent." });
      return sent;
    },
    reject: requestId => {
      if (runtime.inFlightApprovals[requestId]) return false;
      dispatch({ type: "approval_command", requestId });
      const sent = client.sendToolApproval(requestId, false);
      if (!sent) dispatch({ type: "command_error", message: "Runtime link unavailable; rejection was not sent." });
      return sent;
    },
    dismiss: id => {
      const sent = client.dismissPresentation(id);
      if (!sent) dispatch({ type: "command_error", message: "Runtime link unavailable; dismissal was not sent." });
      return sent;
    },
    sendChat: text => {
      const sent = client.sendChat(text);
      if (!sent) dispatch({ type: "command_error", message: "Runtime link unavailable; message was not sent." });
      return sent;
    },
    focusTask: taskId => {
      const sent = client.focusTask(taskId);
      if (!sent) dispatch({ type: "command_error", message: "Runtime link unavailable; task focus was not sent." });
      return sent;
    },
    mediaControl,
  }), [client, mediaControl, runtime.inFlightApprovals]);

  return { runtime, client, actions };
}

export type { RuntimeAction };
