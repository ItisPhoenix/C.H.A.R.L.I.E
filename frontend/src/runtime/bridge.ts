import contract from "../../../shared/event_contract.json";
import { useCharlieStore } from "../store/charlie";

export const EVENT_CONTRACT = contract;
export type KnownEventType = keyof typeof EVENT_CONTRACT.event_types;

export interface WSEvent {
  type: string;
  version?: number;
  id?: string;
  timestamp?: string;
  source?: string;
  session_id?: string | null;
  task_id?: string | null;
  replay?: boolean;
  rationale?: string;
  payload?: Record<string, unknown>;
}

export interface ValidatedWSEvent extends WSEvent {
  type: KnownEventType;
  version: 1;
  id: string;
  timestamp: string;
  source: string;
  session_id: string | null;
  task_id: string | null;
  replay: boolean;
  payload: Record<string, unknown>;
}

const _BASE_DELAY_MS = 3000;
const _MAX_DELAY_MS = 30000;
const _SEEN_EVENT_IDS = new Set<string>();

export function reconnectDelayMs(attempt: number): number {
  return Math.min(_BASE_DELAY_MS * 2 ** attempt, _MAX_DELAY_MS);
}

export function resetEventDedupe(): void {
  _SEEN_EVENT_IDS.clear();
}

function newEventId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `legacy-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function adaptEvent(raw: unknown): ValidatedWSEvent | null {
  if (!isRecord(raw) || typeof raw.type !== "string") return null;
  if (!(raw.type in EVENT_CONTRACT.event_types)) return null;

  const definition = EVENT_CONTRACT.event_types[raw.type as KnownEventType];
  const version = raw.version ?? 1;
  if (version !== 1) return null;

  const payload = raw.payload ?? {};
  if (!isRecord(payload)) return null;
  if (definition.required.some((key) => !(key in payload))) return null;

  const id = raw.id ?? newEventId();
  if (typeof id !== "string" || !id) return null;
  if (_SEEN_EVENT_IDS.has(id)) return null;
  _SEEN_EVENT_IDS.add(id);
  if (_SEEN_EVENT_IDS.size > 1024) _SEEN_EVENT_IDS.delete(_SEEN_EVENT_IDS.values().next().value as string);

  const timestamp = raw.timestamp ?? new Date().toISOString();
  if (typeof timestamp !== "string" || !timestamp) return null;
  const source = raw.source ?? "compatibility";
  if (typeof source !== "string" || !source) return null;
  const sessionId = raw.session_id ?? (typeof payload.session_id === "string" ? payload.session_id : null);
  const taskId = raw.task_id ?? null;
  if (sessionId !== null && typeof sessionId !== "string") return null;
  if (taskId !== null && typeof taskId !== "string") return null;
  if (raw.replay !== undefined && typeof raw.replay !== "boolean") return null;

  return {
    type: raw.type as KnownEventType,
    version: 1,
    id,
    timestamp,
    source,
    session_id: sessionId,
    task_id: taskId,
    replay: raw.replay ?? false,
    ...(typeof raw.rationale === "string" ? { rationale: raw.rationale } : {}),
    payload,
  };
}

export function sendCommand(type: string, payload?: Record<string, unknown>): void {
  const message = JSON.stringify({ type, payload });
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(message);
    return;
  }
  if (pendingCommands.length < 50) pendingCommands.push(message);
}

function handleMessage(event: MessageEvent<string>): void {
  let raw: unknown;
  try {
    raw = JSON.parse(event.data);
  } catch {
    return;
  }
  const message = adaptEvent(raw);
  if (message) useCharlieStore.getState().applyEvent(message);
}

let socket: WebSocket | null = null;
let reconnectAttempt = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
const pendingCommands: string[] = [];

function wsUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
}

// Idempotent -- safe to call from a React effect that may re-run under StrictMode.
export function connectBridge(): () => void {
  if (socket) return () => {};

  const open = () => {
    const ws = new WebSocket(wsUrl());
    socket = ws;

    ws.onopen = () => {
      reconnectAttempt = 0;
      useCharlieStore.getState().setConnected(true);
      while (pendingCommands.length > 0 && ws.readyState === WebSocket.OPEN) {
        ws.send(pendingCommands.shift() as string);
      }
    };
    ws.onmessage = handleMessage;
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      socket = null;
      useCharlieStore.getState().setConnected(false);
      const delay = reconnectDelayMs(reconnectAttempt++);
      reconnectTimer = setTimeout(open, delay);
    };
  };
  open();

  return () => {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    socket?.close();
    socket = null;
  };
}
