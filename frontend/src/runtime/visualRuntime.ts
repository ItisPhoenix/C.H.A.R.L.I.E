export type VisualRuntimePhase =
  | "idle"
  | "listening"
  | "transcribing"
  | "thinking"
  | "acting"
  | "speaking"
  | "approval_wait"
  | "success"
  | "error"
  | "recovering"
  | "offline"
  | "degraded";

export interface VisualRuntimeCorrelation {
  sessionId: string | null;
  turnId: string | null;
  taskId: string | null;
  requestId: string | null;
}

export interface VisualRuntimeState {
  phase: VisualRuntimePhase;
  label: string;
  detail: string | null;
  toolName: string | null;
  transcript: string | null;
  correlation: VisualRuntimeCorrelation;
  updatedAt: string;
  expiresAt: number | null;
}

export interface VisualRuntimeEvent {
  type: string;
  timestamp?: string;
  session_id?: string | null;
  turn_id?: string | null;
  task_id?: string | null;
  payload?: Record<string, unknown>;
}

export const INITIAL_VISUAL_RUNTIME: VisualRuntimeState = {
  phase: "offline",
  label: "OFFLINE",
  detail: "Runtime connection unavailable",
  toolName: null,
  transcript: null,
  correlation: { sessionId: null, turnId: null, taskId: null, requestId: null },
  updatedAt: new Date(0).toISOString(),
  expiresAt: null,
};

const COMPLETION_EVENTS = new Set([
  "tool_result",
  "browser_task_done",
  "research_result",
  "speaking_stop",
]);

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function concise(value: unknown, limit = 180): string | null {
  const text = stringValue(value);
  if (!text || /<\/?(?:think|thought|analysis)>/i.test(text)) return null;
  return text.length > limit ? `${text.slice(0, limit - 1).trimEnd()}…` : text;
}

function correlation(event: VisualRuntimeEvent): VisualRuntimeCorrelation {
  const payload = record(event.payload);
  return {
    sessionId: event.session_id ?? stringValue(payload.session_id),
    turnId: event.turn_id ?? stringValue(payload.turn_id),
    taskId: event.task_id ?? stringValue(payload.task_id),
    requestId: stringValue(payload.request_id),
  };
}

function eventTime(event: VisualRuntimeEvent): number | null {
  if (!event.timestamp) return null;
  const parsed = Date.parse(event.timestamp);
  return Number.isFinite(parsed) ? parsed : null;
}

function isStale(previous: VisualRuntimeState, event: VisualRuntimeEvent): boolean {
  const incoming = eventTime(event);
  const current = Date.parse(previous.updatedAt);
  return incoming !== null && Number.isFinite(current) && incoming < current;
}

function mismatchedCompletion(previous: VisualRuntimeState, event: VisualRuntimeEvent): boolean {
  if (!COMPLETION_EVENTS.has(event.type)) return false;
  const next = correlation(event);
  const current = previous.correlation;
  const hasNextCorrelation = Object.values(next).some(Boolean);
  const hasCurrentCorrelation = Object.values(current).some(Boolean);
  if (!hasNextCorrelation && hasCurrentCorrelation) return true;
  return Boolean(
    (next.sessionId && current.sessionId && next.sessionId !== current.sessionId) ||
    (next.turnId && current.turnId && next.turnId !== current.turnId) ||
    (next.taskId && current.taskId && next.taskId !== current.taskId) ||
    (next.requestId && current.requestId && next.requestId !== current.requestId),
  );
}

function ambientUpdateAllowed(previous: VisualRuntimeState): boolean {
  return ["idle", "success", "degraded"].includes(previous.phase);
}

function nextState(
  previous: VisualRuntimeState,
  event: VisualRuntimeEvent,
  phase: VisualRuntimePhase,
  label: string,
  detail: string | null = null,
  options: { toolName?: string | null; transcript?: string | null; expiresMs?: number | null } = {},
): VisualRuntimeState {
  const timestamp = event.timestamp && Number.isFinite(Date.parse(event.timestamp))
    ? new Date(event.timestamp).toISOString()
    : new Date().toISOString();
  const incoming = correlation(event);
  const sessionChanged = Boolean(incoming.sessionId && incoming.sessionId !== previous.correlation.sessionId);
  const turnChanged = Boolean(incoming.turnId && incoming.turnId !== previous.correlation.turnId);
  const taskChanged = Boolean(incoming.taskId && incoming.taskId !== previous.correlation.taskId);
  return {
    phase,
    label,
    detail,
    toolName: options.toolName ?? null,
    transcript: options.transcript ?? null,
    correlation: {
      sessionId: incoming.sessionId ?? previous.correlation.sessionId,
      turnId: incoming.turnId ?? (sessionChanged ? null : previous.correlation.turnId),
      taskId: incoming.taskId ?? (sessionChanged || turnChanged ? null : previous.correlation.taskId),
      requestId: incoming.requestId ?? (sessionChanged || turnChanged || taskChanged ? null : previous.correlation.requestId),
    },
    updatedAt: timestamp,
    expiresAt: options.expiresMs ? Date.now() + options.expiresMs : null,
  };
}

function resultFailed(payload: Record<string, unknown>): boolean {
  const status = String(payload.status ?? "").toLowerCase();
  return payload.success === false || ["failed", "error", "cancelled", "rejected"].includes(status);
}

function degradedHealth(payload: Record<string, unknown>): boolean {
  return Object.values(payload).some((value) => {
    const status = String(record(value).status ?? "").toLowerCase();
    return ["degraded", "error", "stopped", "unavailable"].includes(status);
  });
}

export function reduceVisualRuntime(previous: VisualRuntimeState, event: VisualRuntimeEvent): VisualRuntimeState {
  if (isStale(previous, event) || mismatchedCompletion(previous, event)) return previous;

  const payload = record(event.payload);
  switch (event.type) {
    case "charlie_state": {
      const state = String(payload.state ?? "idle").toLowerCase();
      if (state === "idle") return nextState(previous, event, "idle", "IDLE");
      if (state === "listening") return nextState(previous, event, "listening", "LISTENING", "Awaiting input");
      if (state === "thinking") return nextState(previous, event, "thinking", "THINKING");
      if (state === "speaking") return nextState(previous, event, "speaking", "SPEAKING");
      if (["working", "executing"].includes(state)) return nextState(previous, event, "acting", "ACTING");
      if (state === "waiting") return nextState(previous, event, "approval_wait", "APPROVAL WAIT");
      if (state === "attention") return nextState(previous, event, "error", "ATTENTION");
      if (state === "completed") return nextState(previous, event, "success", "SUCCESS", null, { expiresMs: 2200 });
      if (state === "error") return nextState(previous, event, "error", "ERROR", null, { expiresMs: 4000 });
      return previous;
    }
    case "transcript": {
      const text = concise(payload.text);
      return text ? nextState(previous, event, "transcribing", "TRANSCRIBING", text, { transcript: text }) : previous;
    }
    case "thinking_update": {
      const text = concise(payload.display_text ?? payload.text ?? payload.message);
      return text ? nextState(previous, event, "thinking", "THINKING", text) : previous;
    }
    case "tool_call": {
      const name = concise(payload.name ?? payload.tool_name, 80) ?? "TOOL";
      return nextState(previous, event, "acting", `USING ${name.toUpperCase()}`, null, { toolName: name });
    }
    case "tool_result": {
      const failed = resultFailed(payload);
      return nextState(
        previous,
        event,
        failed ? "error" : "success",
        failed ? "TOOL ERROR" : "TOOL COMPLETE",
        concise(payload.display_text ?? payload.text ?? payload.message),
        { toolName: concise(payload.name ?? payload.tool_name, 80), expiresMs: failed ? 5000 : 2200 },
      );
    }
    case "alert": {
      const severity = String(payload.severity ?? "info").toLowerCase();
      const detail = concise(payload.message ?? payload.text);
      if (severity === "warning") {
        return ambientUpdateAllowed(previous)
          ? nextState(previous, event, "degraded", "DEGRADED", detail, { expiresMs: 5000 })
          : previous;
      }
      if (["error", "critical"].includes(severity)) return nextState(previous, event, "error", "ERROR", detail, { expiresMs: 5000 });
      return detail && ambientUpdateAllowed(previous)
        ? nextState(previous, event, "success", "NOTICE", detail, { expiresMs: 2200 })
        : previous;
    }
    case "recovery_proposal":
      return nextState(previous, event, "recovering", "RECOVERY AVAILABLE", concise(payload.message ?? payload.reason ?? payload.summary));
    case "speaking_start":
      return nextState(previous, event, "speaking", "SPEAKING");
    case "speaking_stop":
      return previous.phase === "speaking" ? nextState(previous, event, "idle", "IDLE") : previous;
    case "subsystem_health":
      return degradedHealth(payload) && ambientUpdateAllowed(previous)
        ? nextState(previous, event, "degraded", "DEGRADED", "Subsystem health requires attention")
        : previous;
    case "system_status":
      // Telemetry belongs to the system-status projection; it must not imply activity.
      return previous;
    case "browser_task_started":
      return nextState(previous, event, "acting", "BROWSER ACTING", concise(payload.message ?? payload.operation));
    case "browser_task_done":
      return nextState(previous, event, resultFailed(payload) ? "error" : "success", resultFailed(payload) ? "BROWSER ERROR" : "BROWSER COMPLETE", concise(payload.message ?? payload.result), { expiresMs: 2200 });
    case "research_progress":
      return nextState(previous, event, "acting", `RESEARCH ${String(payload.stage ?? "ACTIVE").toUpperCase()}`, concise(payload.message));
    case "research_result":
      return nextState(previous, event, resultFailed(payload) ? "error" : "success", resultFailed(payload) ? "RESEARCH ERROR" : "RESEARCH READY", concise(payload.summary ?? payload.message), { expiresMs: resultFailed(payload) ? 5000 : 2200 });
    case "vision_observed":
      return nextState(previous, event, resultFailed(payload) ? "error" : "success", resultFailed(payload) ? "VISION ERROR" : "VISION READY", concise(payload.summary ?? payload.message), { expiresMs: resultFailed(payload) ? 5000 : 2200 });
    default:
      return previous;
  }
}

export function setVisualRuntimeConnection(previous: VisualRuntimeState, connected: boolean): VisualRuntimeState {
  if (!connected) {
    return {
      ...INITIAL_VISUAL_RUNTIME,
      updatedAt: new Date().toISOString(),
    };
  }
  return previous.phase === "offline"
    ? { ...previous, phase: "idle", label: "IDLE", detail: null, updatedAt: new Date().toISOString() }
    : previous;
}
