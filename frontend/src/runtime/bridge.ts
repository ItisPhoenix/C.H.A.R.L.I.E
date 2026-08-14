import { useCharlieStore } from "../store/charlie";

// Event vocabulary per CLAUDE.md sec8.5 -- extend here, not with ad hoc string checks at call sites.
export interface WSEvent {
  type: string;
  session_id?: string;
  payload?: Record<string, unknown>;
}

const _BASE_DELAY_MS = 3000;
const _MAX_DELAY_MS = 30000;

export function reconnectDelayMs(attempt: number): number {
  return Math.min(_BASE_DELAY_MS * 2 ** attempt, _MAX_DELAY_MS);
}

let socket: WebSocket | null = null;
let reconnectAttempt = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
const pendingCommands: string[] = [];

function wsUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
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
  let msg: WSEvent;
  try {
    msg = JSON.parse(event.data);
  } catch {
    return;
  }
  useCharlieStore.getState().applyEvent(msg);
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
