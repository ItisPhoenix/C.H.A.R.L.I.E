import contract from "../../../shared/event_contract.json";

export const EVENT_CONTRACT = contract;
export type KnownEventType = keyof typeof EVENT_CONTRACT.event_types;

export interface RuntimeWireEvent {
  type: string;
  version?: number;
  id?: string;
  timestamp?: string;
  source?: string;
  session_id?: string | null;
  task_id?: string | null;
  turn_id?: string | null;
  correlation_id?: string | null;
  replay?: boolean;
  rationale?: string;
  payload?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ValidatedRuntimeEvent extends RuntimeWireEvent {
  type: KnownEventType;
  version: 1;
  id: string;
  timestamp: string;
  source: string;
  session_id: string | null;
  task_id: string | null;
  turn_id: string | null;
  replay: boolean;
  payload: Record<string, unknown>;
}

const SEEN_EVENT_IDS = new Set<string>();

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nullableString(value: unknown): string | null | undefined {
  if (value === undefined || value === null) return value;
  return typeof value === "string" ? value : undefined;
}

export function resetEventDedupe(): void {
  SEEN_EVENT_IDS.clear();
}

export function adaptEvent(raw: unknown): ValidatedRuntimeEvent | null {
  if (!isRecord(raw) || typeof raw.type !== "string") return null;
  if (!(raw.type in EVENT_CONTRACT.event_types)) return null;
  if (raw.version !== 1 || typeof raw.id !== "string" || !raw.id.trim()) return null;
  if (typeof raw.timestamp !== "string" || !raw.timestamp.trim()) return null;
  if (typeof raw.source !== "string" || !raw.source.trim()) return null;
  if (typeof raw.replay !== "boolean") return null;

  const payload = raw.payload;
  if (!isRecord(payload)) return null;
  const definition = EVENT_CONTRACT.event_types[raw.type as KnownEventType];
  if (definition.required.some((key) => !(key in payload))) return null;

  const sessionId = nullableString(raw.session_id);
  const taskId = nullableString(raw.task_id);
  const turnId = nullableString(raw.turn_id);
  if (sessionId === undefined || taskId === undefined || turnId === undefined) return null;
  if (SEEN_EVENT_IDS.has(raw.id)) return null;
  SEEN_EVENT_IDS.add(raw.id);
  if (SEEN_EVENT_IDS.size > 1024) {
    const oldest = SEEN_EVENT_IDS.values().next().value;
    if (typeof oldest === "string") SEEN_EVENT_IDS.delete(oldest);
  }

  return {
    type: raw.type as KnownEventType,
    version: 1,
    id: raw.id,
    timestamp: raw.timestamp,
    source: raw.source,
    session_id: sessionId,
    task_id: taskId,
    turn_id: turnId,
    replay: raw.replay,
    ...(typeof raw.correlation_id === "string" ? { correlation_id: raw.correlation_id } : {}),
    ...(typeof raw.rationale === "string" ? { rationale: raw.rationale } : {}),
    payload,
  };
}

export function createLocalEvent(
  type: string,
  payload: Record<string, unknown>,
  identity: Partial<Pick<RuntimeWireEvent, "session_id" | "task_id" | "turn_id" | "correlation_id">> = {},
): ValidatedRuntimeEvent | null {
  const id = globalThis.crypto?.randomUUID?.() ?? `frontend-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return adaptEvent({
    type,
    version: 1,
    id,
    timestamp: new Date().toISOString(),
    source: "frontend",
    replay: false,
    session_id: identity.session_id ?? null,
    task_id: identity.task_id ?? null,
    turn_id: identity.turn_id ?? null,
    ...identity,
    payload,
  });
}

export function shouldQueueCommand(type: string): boolean {
  return type !== "recovery_approve" && type !== "recovery_reject";
}
