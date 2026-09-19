import { coreStates, type CoreState } from "../components/CharlieCore";
import type {
  RuntimeActivity,
  RuntimeApproval,
  RuntimeEvent,
  RuntimeMediaSnapshot,
  RuntimePresentation,
  RuntimeState,
  RuntimeTask,
} from "./types";
import type { RuntimeWidgetState } from "./widgets";
import { inferWidgetId } from "./widgets";
import { EVENT_CONTRACT } from "./bridge";
import { normalizeResearchPayload, researchTruthForPayload } from "../research/payload";

export const runtimeEventTypes = new Set(Object.keys(EVENT_CONTRACT.event_types));

const sessionScopedEvents = new Set([
  "token", "transcript", "desktop_frame", "thinking_update", "tool_call", "tool_result",
  "research_progress", "research_result", "response_done", "speaking_start", "speaking_stop",
  "presentation_intent", "presentation_update", "presentation_dismiss",
]);

const activeTaskStatuses = new Set([
  "queued", "planning", "waiting", "running", "paused", "approval_required", "awaiting_approval", "verifying",
]);

const payloadOf = (event: RuntimeEvent): Record<string, unknown> =>
  event.payload && typeof event.payload === "object" ? event.payload : {};

const stringValue = (value: unknown, fallback = "") =>
  typeof value === "string" ? value.trim() : fallback;

const firstText = (payload: Record<string, unknown>, ...keys: string[]) => {
  for (const key of keys) {
    const value = stringValue(payload[key]);
    if (value) return value;
  }
  return "";
};

const numberValue = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

const objectValue = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;

const compactValue = (value: unknown, limit = 180) => {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (!text) return "";
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
};

function mediaSnapshotFromPayload(payload: Record<string, unknown>): RuntimeMediaSnapshot | null {
  const result = objectValue(payload.result);
  const data = objectValue(result?.data);
  const structured = objectValue(data?.structured_data);
  const media = structured ?? objectValue(payload.media) ??
    (typeof payload.available === "boolean" ? payload : null);
  if (!media || typeof media.available !== "boolean") return null;
  return media as RuntimeMediaSnapshot;
}

const eventSessionId = (event: RuntimeEvent, payload: Record<string, unknown>) =>
  stringValue(event.session_id) || stringValue(payload.session_id) || null;

const eventTurnId = (event: RuntimeEvent, payload: Record<string, unknown>) =>
  stringValue(event.turn_id) || stringValue(payload.turn_id) || null;

const eventTaskId = (event: RuntimeEvent, payload: Record<string, unknown>) =>
  stringValue(event.task_id) || stringValue(payload.task_id) || null;

function normalizeCoreState(value: unknown, fallback: CoreState): CoreState {
  const state = stringValue(value);
  if (state === "working" || state === "processing") return state === "working" ? "acting" : "thinking";
  if (state === "waiting" || state === "approval_required") return "waiting-for-approval";
  return (coreStates as readonly string[]).includes(state) ? state as CoreState : fallback;
}

function normalizeTask(value: unknown): RuntimeTask | null {
  const task = objectValue(value);
  if (!task) return null;
  const rawId = task.id ?? task.task_id;
  if (rawId === undefined || rawId === null || String(rawId).trim() === "") return null;
  return {
    ...task,
    id: String(rawId),
    status: stringValue(task.status) || undefined,
    title: stringValue(task.title) || stringValue(task.text) || undefined,
  };
}

function taskMap(values: unknown): Record<string, RuntimeTask> {
  if (!Array.isArray(values)) return {};
  return Object.fromEntries(values.map(normalizeTask).filter((task): task is RuntimeTask => task !== null)
    .map(task => [task.id, task]));
}

function activeTasks(tasks: Record<string, RuntimeTask>) {
  return Object.values(tasks).filter(task => activeTaskStatuses.has(stringValue(task.status)));
}

function finishState(state: RuntimeState): CoreState {
  if (Object.keys(state.approvals).length) return "waiting-for-approval";
  if (activeTasks(state.tasks).length || state.activeActivity?.kind === "tool") return "acting";
  return "idle";
}

function withEvent(state: RuntimeState, type: string): RuntimeState {
  return { ...state, lastEventType: type };
}

function eventTimestampIsOlder(incoming: string | undefined, current: string | null | undefined): boolean {
  if (!incoming || !current) return false;
  const incomingTime = Date.parse(incoming);
  const currentTime = Date.parse(current);
  return Number.isFinite(incomingTime) && Number.isFinite(currentTime) && incomingTime < currentTime;
}

function researchEventIsStale(state: RuntimeState, event: RuntimeEvent, payload: Record<string, unknown>): boolean {
  if (eventTimestampIsOlder(event.timestamp, state.research.updatedAt)) return true;
  const incomingCorrelation = firstText(payload, "correlation_id", "correlationId") || stringValue(event.correlation_id);
  if (state.research.correlationId && incomingCorrelation && state.research.correlationId !== incomingCorrelation) {
    return eventTimestampIsOlder(event.timestamp, state.research.updatedAt);
  }
  return false;
}

function researchIdentity(state: RuntimeState, event: RuntimeEvent, payload: Record<string, unknown>) {
  return {
    sessionId: eventSessionId(event, payload) ?? state.research.sessionId ?? state.sessionId,
    taskId: stringValue(event.task_id) || stringValue(payload.task_id) || state.research.taskId || null,
    turnId: eventTurnId(event, payload) ?? state.research.turnId ?? null,
    correlationId: firstText(payload, "correlation_id", "correlationId") || stringValue(event.correlation_id) || state.research.correlationId || null,
  };
}

function researchActivityId(event: RuntimeEvent, stage: string, index: number): string {
  return event.id || `${stage}-${event.timestamp || index}`;
}

function approvalFromPayload(
  payload: Record<string, unknown>,
  event: RuntimeEvent,
): RuntimeApproval | null {
  const requestId = stringValue(payload.request_id);
  if (!requestId) return null;
  const content = objectValue(payload.content);
  const source = content ?? payload;
  return {
    requestId,
    toolName: firstText(source, "tool_name", "name") || "Action",
    reason: firstText(source, "reason", "summary", "message") || "A decision is required.",
    riskClass: firstText(source, "risk_class", "risk") || undefined,
    arguments: objectValue(source.arguments) ?? objectValue(source.args) ?? undefined,
    taskId: stringValue(event.task_id) || stringValue(payload.task_id) || null,
    turnId: eventTurnId(event, payload),
  };
}

export function createInitialRuntimeState(): RuntimeState {
  return {
    connection: "connecting",
    sessionId: null,
    coreState: "idle",
    conversation: { userText: "", responseText: "", streaming: false, turnId: null },
    transcript: "",
    thinking: "",
    activeActivity: null,
    lastResult: null,
    tasks: {},
    approvals: {},
    inFlightApprovals: {},
    research: { progress: null, result: null, activity: [], truth: "NOT VERIFIED", error: null },
    terminal: null,
    systemStatus: {},
    subsystemHealth: {},
    runtimeTruth: null,
    telemetry: null,
    presentations: {},
    widgets: {},
    media: null,
    mediaPending: false,
    mediaError: null,
    audioState: {},
    micState: {},
    lastAlert: null,
    lastError: null,
    lastEventType: null,
    seenEventIds: [],
  };
}

export type RuntimeAction =
  | { type: "event"; event: RuntimeEvent }
  | { type: "connection"; status: RuntimeState["connection"] }
  | { type: "session"; sessionId: string | null }
  | { type: "approval_command"; requestId: string }
  | { type: "command_error"; message: string }
  | { type: "media_loading" }
  | { type: "media_snapshot"; snapshot: RuntimeMediaSnapshot | null }
  | { type: "media_error"; message: string };

export function runtimeReducer(state: RuntimeState, action: RuntimeAction): RuntimeState {
  if (action.type === "connection") return { ...state, connection: action.status };
  if (action.type === "session") return { ...state, sessionId: action.sessionId };
  if (action.type === "approval_command") {
    if (!state.approvals[action.requestId] || state.inFlightApprovals[action.requestId]) return state;
    return {
      ...state,
      inFlightApprovals: { ...state.inFlightApprovals, [action.requestId]: true },
      lastError: null,
    };
  }
  if (action.type === "command_error") {
    return { ...state, lastError: action.message };
  }
  if (action.type === "media_loading") {
    return { ...state, mediaPending: true, mediaError: null };
  }
  if (action.type === "media_snapshot") {
    return { ...state, media: action.snapshot, mediaPending: false, mediaError: null };
  }
  if (action.type === "media_error") {
    return { ...state, mediaPending: false, mediaError: action.message };
  }

  const event = action.event;
  if (!event || typeof event.type !== "string" || !runtimeEventTypes.has(event.type)) return state;
  const payload = payloadOf(event);
  const sessionId = eventSessionId(event, payload);
  if (state.sessionId && sessionId && sessionId !== state.sessionId && sessionScopedEvents.has(event.type)) {
    return state;
  }

  if (event.id && state.seenEventIds.includes(event.id)) return state;
  const seenEventIds = event.id
    ? [...state.seenEventIds, event.id].slice(-512)
    : state.seenEventIds;
  const eventState = seenEventIds === state.seenEventIds ? state : { ...state, seenEventIds };
  const next = withEvent(eventState, event.type);
  switch (event.type) {
    case "session_active":
    case "session_updated":
      return { ...next, sessionId: stringValue(payload.session_id) || null };

    case "charlie_state":
      return { ...next, coreState: normalizeCoreState(payload.state, state.coreState) };

    case "chat":
    case "transcript": {
      const text = firstText(payload, "text", "message", "transcript");
      if (!text) return next;
      return {
        ...next,
        transcript: event.type === "transcript" ? text : state.transcript,
        thinking: "",
        conversation: { userText: text, responseText: "", streaming: false, turnId: eventTurnId(event, payload) },
        lastResult: null,
        coreState: event.type === "transcript" ? "listening" : "thinking",
      };
    }

    case "thinking":
    case "thinking_update": {
      if (eventTaskId(event, payload)) {
        return { ...next, thinking: "", coreState: state.research.progress ? "acting" : state.coreState };
      }
      const thinking = firstText(payload, "text", "message", "stage") || state.thinking;
      return { ...next, thinking, coreState: "thinking" };
    }

    case "token": {
      const token = firstText(payload, "text", "token", "delta", "content");
      if (!token) return next;
      const turnId = eventTurnId(event, payload) || state.conversation.turnId;
      const newTurn = Boolean(turnId && state.conversation.turnId && turnId !== state.conversation.turnId);
      return {
        ...next,
        thinking: "",
        conversation: {
          ...state.conversation,
          responseText: newTurn || !state.conversation.streaming ? token : `${state.conversation.responseText}${token}`,
          streaming: true,
          turnId,
        },
        coreState: "speaking",
      };
    }

    case "response_done": {
      const finalText = firstText(payload, "text", "response", "content", "result");
      return {
        ...next,
        conversation: {
          ...state.conversation,
          responseText: finalText || state.conversation.responseText,
          streaming: false,
        },
        activeActivity: null,
        coreState: finishState(state),
      };
    }

    case "speaking_start":
      return { ...next, coreState: "speaking" };
    case "speaking_stop":
      return { ...next, coreState: finishState(state) };
    case "vad_start":
      return { ...next, coreState: "listening" };
    case "audio_state":
      return { ...next, audioState: payload };
    case "mic_state":
      return { ...next, micState: payload };

    case "media_operation_result": {
      const snapshot = mediaSnapshotFromPayload(payload);
      return snapshot ? { ...next, media: snapshot, mediaPending: false, mediaError: null } : next;
    }

    case "tool_call": {
      const name = firstText(payload, "name", "tool_name") || "Tool action";
      const args = payload.args ?? payload.arguments;
      const activity: RuntimeActivity = {
        kind: "tool", label: name, detail: args ? compactValue(args) : undefined,
        taskId: stringValue(event.task_id) || stringValue(payload.task_id) || null,
      };
      return { ...next, activeActivity: activity, coreState: "acting" };
    }

    case "tool_result": {
      const name = firstText(payload, "name", "tool_name") || "Tool result";
      const summary = firstText(payload, "text", "result", "message") || compactValue(payload.result) || "Completed.";
      return {
        ...next,
        activeActivity: null,
        lastResult: { kind: "tool", title: name, summary, taskId: stringValue(event.task_id) || null },
        coreState: finishState(state),
      };
    }

    case "background_task": {
      const task = normalizeTask(payload.task ?? payload);
      if (!task) return next;
      const tasks = { ...state.tasks, [task.id]: task };
      const status = stringValue(task.status);
      const activity = activeTaskStatuses.has(status)
        ? { kind: "task" as const, label: task.title || `Task ${task.id}`, detail: status, taskId: task.id }
        : state.activeActivity?.kind === "task" && state.activeActivity.taskId === task.id ? null : state.activeActivity;
      const hasActiveTasks = activeTasks(tasks).length > 0;
      const taskWidget = state.widgets.tasks?.lifecycle === "dismissed"
        ? state.widgets.tasks
        : hasActiveTasks
          ? { lifecycle: state.widgets.tasks?.lifecycle === "expanded" ? "expanded" as const : "compact" as const,
            sourceId: state.widgets.tasks?.sourceId ?? task.id }
          : state.widgets.tasks?.lifecycle === "compact"
            ? { ...state.widgets.tasks, lifecycle: "collapsed" as const }
            : state.widgets.tasks;
      return {
        ...next,
        tasks,
        widgets: taskWidget ? { ...state.widgets, tasks: taskWidget } : state.widgets,
        activeActivity: activity,
        coreState: activeTaskStatuses.has(status) ? "acting" : finishState({ ...state, tasks }),
      };
    }

    case "task_snapshot": {
      const tasks = taskMap(payload.tasks);
      const current = activeTasks(tasks)[0];
      const existingTaskWidget = state.widgets.tasks;
      const taskWidget: RuntimeWidgetState | undefined = current && existingTaskWidget?.lifecycle !== "dismissed"
        ? { lifecycle: existingTaskWidget?.lifecycle === "expanded" ? "expanded" : "compact",
          sourceId: existingTaskWidget?.sourceId ?? current.id }
        : !current && existingTaskWidget?.lifecycle === "compact"
          ? { ...existingTaskWidget, lifecycle: "collapsed" }
          : existingTaskWidget;
      return {
        ...next,
        tasks,
        widgets: taskWidget ? { ...state.widgets, tasks: taskWidget } : state.widgets,
        activeActivity: current
          ? { kind: "task", label: current.title || `Task ${current.id}`, detail: stringValue(current.status), taskId: current.id }
          : state.activeActivity?.kind === "task" ? null : state.activeActivity,
        coreState: activeTasks(tasks).length ? "acting" : finishState({ ...state, tasks }),
      };
    }

    case "terminal_command_result": {
      const command = firstText(payload, "command") || "Terminal command";
      const summary = firstText(payload, "result", "text", "message") || "No output.";
      return {
        ...next,
        terminal: payload,
        activeActivity: null,
        lastResult: { kind: "terminal", title: command, summary, detail: firstText(payload, "status", "approval_status"), taskId: stringValue(event.task_id) || null },
        coreState: finishState(state),
      };
    }

    case "tool_approval_request": {
      const approval = approvalFromPayload(payload, event);
      if (!approval) return next;
      return {
        ...next,
        approvals: { ...state.approvals, [approval.requestId]: approval },
        coreState: "waiting-for-approval",
      };
    }

    case "tool_approval_resolved": {
      const requestId = stringValue(payload.request_id);
      if (!requestId) return next;
      const approvals = { ...state.approvals };
      delete approvals[requestId];
      const inFlightApprovals = { ...state.inFlightApprovals };
      delete inFlightApprovals[requestId];
      return { ...next, approvals, inFlightApprovals, coreState: finishState({ ...state, approvals }) };
    }

    case "research_progress": {
      if (researchEventIsStale(state, event, payload)) return state;
      const stage = firstText(payload, "stage") || "Researching";
      const message = firstText(payload, "message", "text") || stage;
      const identity = researchIdentity(state, event, payload);
      const activity = {
        id: researchActivityId(event, stage, (state.research.activity ?? []).length),
        stage,
        message,
        current: numberValue(payload.current),
        total: numberValue(payload.total),
        mode: firstText(payload, "mode") || undefined,
      };
      const activities = [...(state.research.activity ?? []).filter(item => item.id !== activity.id), activity].slice(-24);
      return {
        ...next,
        research: {
          ...state.research,
          progress: { stage, message, current: numberValue(payload.current), total: numberValue(payload.total), mode: firstText(payload, "mode") || undefined },
          objective: firstText(payload, "objective", "query") || state.research.objective || state.conversation.userText || null,
          ...identity,
          activity: activities,
          updatedAt: event.timestamp ?? state.research.updatedAt ?? null,
          error: null,
          truth: state.research.truth === "FIXTURE" ? "FIXTURE" : "NOT VERIFIED",
        },
        widgets: { ...state.widgets, research: {
            lifecycle: state.widgets.research?.lifecycle === "expanded" ? "expanded" as const : "compact" as const,
            sourceId: state.widgets.research?.sourceId,
          } },
        activeActivity: { kind: "research", label: stage, detail: message, taskId: stringValue(event.task_id) || null },
        coreState: "acting",
      };
    }

    case "research_result": {
      if (researchEventIsStale(state, event, payload)) return state;
      const result = normalizeResearchPayload(payload);
      const identity = researchIdentity(state, event, payload);
      const summary = firstText(payload, "summary", "answer", "message") || "Research result ready.";
      return {
        ...next,
        research: {
          ...state.research,
          progress: null,
          result: result as Record<string, unknown> | null,
          ...identity,
          objective: result?.objective || result?.query || state.research.objective || state.conversation.userText || null,
          updatedAt: event.timestamp ?? state.research.updatedAt ?? null,
          error: result ? null : "Research result did not satisfy the shared workspace contract.",
          truth: result ? researchTruthForPayload(result) : "NOT VERIFIED",
        },
        widgets: state.widgets.research?.lifecycle === "dismissed"
          ? state.widgets
          : { ...state.widgets, research: {
            lifecycle: state.widgets.research?.lifecycle === "expanded" ? "expanded" as const : "compact" as const,
            sourceId: state.widgets.research?.sourceId,
          } },
        activeActivity: null,
        lastResult: { kind: "research", title: firstText(payload, "title", "query") || "Research result", summary, taskId: stringValue(event.task_id) || null },
        coreState: finishState(state),
      };
    }

    case "system_status":
      return { ...next, systemStatus: payload };
    case "subsystem_health":
      return { ...next, subsystemHealth: payload };
    case "runtime_truth": {
      const status = stringValue(payload.status);
      return { ...next, runtimeTruth: payload, coreState: ["degraded", "failed", "unavailable", "stopped"].includes(status) ? "degraded" : state.coreState };
    }
    case "runtime_telemetry":
      return { ...next, telemetry: payload };

    case "presentation_intent":
    case "presentation_update": {
      const id = stringValue(payload.id);
      if (!id) return next;
      const previous = state.presentations[id];
      const merged = previous ? { ...previous, ...payload } : payload;
      const presentation: RuntimePresentation = {
        ...merged,
        id,
        kind: firstText(merged, "kind") || "widget",
        title: firstText(merged, "title") || undefined,
        summary: firstText(merged, "summary", "message") || undefined,
        workspaceType: firstText(merged, "workspace_type", "workspaceType") || undefined,
        priority: numberValue(merged.priority),
        taskId: stringValue(event.task_id) || stringValue(merged.task_id) || null,
        sessionId: eventSessionId(event, merged),
        turnId: eventTurnId(event, merged),
        correlationId: firstText(merged, "correlation_id", "correlationId") || null,
      };
      const approval = presentation.kind === "attention" ? approvalFromPayload(payload, event) : null;
      const widgetId = inferWidgetId(presentation);
      const expanded = presentation.kind === "workspace" || payload.expanded === true;
      const researchPresentation = widgetId === "research" && presentation.kind === "workspace"
        ? normalizeResearchPayload(presentation.content)
        : null;
      const research = researchPresentation && !researchEventIsStale(state, event, payload)
        ? {
          ...state.research,
          progress: null,
          result: researchPresentation as Record<string, unknown>,
          objective: researchPresentation.objective || researchPresentation.query,
          sessionId: eventSessionId(event, payload) ?? state.research.sessionId ?? state.sessionId,
          taskId: stringValue(event.task_id) || stringValue(merged.task_id) || state.research.taskId || null,
          turnId: eventTurnId(event, payload) ?? state.research.turnId ?? null,
          correlationId: firstText(payload, "correlation_id", "correlationId") || state.research.correlationId || null,
          updatedAt: event.timestamp ?? state.research.updatedAt ?? null,
          error: null,
          truth: researchTruthForPayload(researchPresentation),
          presentationId: id,
        }
        : state.research;
      const widgetState = widgetId ? {
        ...state.widgets,
        [widgetId]: {
          lifecycle: expanded && widgetId !== "conversation" ? "expanded" as const : "summoned" as const,
          sourceId: id,
        },
      } : state.widgets;
      return {
        ...next,
        presentations: { ...state.presentations, [id]: presentation },
        widgets: widgetState,
        approvals: approval ? { ...state.approvals, [approval.requestId]: approval } : state.approvals,
        research,
        coreState: approval ? "waiting-for-approval" : state.coreState,
      };
    }

    case "presentation_dismiss": {
      const id = stringValue(payload.id);
      if (!id) return next;
      const dismissedPresentation = state.presentations[id];
      const presentations = { ...state.presentations };
      delete presentations[id];
      const approvals = { ...state.approvals };
      delete approvals[id];
      const widgetId = inferWidgetId(dismissedPresentation ?? payload);
      const widgets = widgetId
        ? { ...state.widgets, [widgetId]: { lifecycle: "dismissed" as const, sourceId: id } }
        : state.widgets;
      return { ...next, presentations, widgets, approvals, coreState: finishState({ ...state, presentations, approvals }) };
    }

    case "alert": {
      const message = firstText(payload, "message", "text") || "Runtime alert.";
      const severity = firstText(payload, "severity");
      return { ...next, lastAlert: message, lastError: severity === "error" ? message : state.lastError, coreState: severity === "error" ? "degraded" : state.coreState };
    }
  }
  return next;
}
