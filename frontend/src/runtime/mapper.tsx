import type { ReactNode } from "react";
import type { ProjectionData } from "../components/Projection";
import type { CoreState } from "../components/CharlieCore";
import { ChatWidget, MediaWidget, SystemWidget, TaskWidget, type SystemIssue } from "../components/RuntimeWidgets";
import { ResearchWorkspace } from "../components/ResearchWorkspace";
import { normalizeResearchPayload } from "../research/payload";
import type { RuntimeActions, RuntimePresentation, RuntimeState, RuntimeTask } from "./types";
import { getWidgetDefinition, inferWidgetId, isWidgetExpanded, isWidgetVisible, type WidgetId } from "./widgets";

export interface RuntimeScene {
  items: ProjectionData[];
  state: CoreState;
}

const activeStatuses = new Set([
  "queued", "planning", "waiting", "running", "paused", "approval_required", "awaiting_approval", "verifying",
]);
const badStatuses = new Set(["degraded", "failed", "unavailable", "stopped", "error"]);

const text = (value: unknown, fallback = "") =>
  typeof value === "string" && value.trim() ? value.trim() : fallback;

const record = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;

const taskStatus = (task: RuntimeTask) => text(task.status, "queued");

const activeTasks = (runtime: RuntimeState) =>
  Object.values(runtime.tasks).filter(task => activeStatuses.has(taskStatus(task)));

const widgetPresentation = (runtime: RuntimeState, id: WidgetId) => {
  const sourceId = runtime.widgets[id]?.sourceId;
  return (sourceId ? runtime.presentations[sourceId] : undefined) ??
    Object.values(runtime.presentations).find(p => inferWidgetId(p) === id);
};

const widgetDismiss = (runtime: RuntimeState, id: WidgetId, actions: RuntimeActions) => {
  const presentation = widgetPresentation(runtime, id);
  return presentation ? () => { actions.dismiss(presentation.id); } : undefined;
};

const widgetProjection = (runtime: RuntimeState, id: WidgetId, overrides: Partial<ProjectionData> = {}): ProjectionData => {
  const definition = getWidgetDefinition(id)!;
  const lifecycle = runtime.widgets[id]?.lifecycle;
  const aroundCore = definition.preferredZone === "around-core";
  return {
    id: `runtime-${id}`,
    widgetId: id,
    widgetLifecycle: lifecycle,
    preferredFootprint: definition.preferredFootprint,
    preferredZone: definition.preferredZone,
    relatedTo: aroundCore ? "charlie" : undefined,
    immersive: isWidgetExpanded(lifecycle) && definition.immersiveWhenExpanded,
    importance: definition.priority,
    depth: "active",
    treatment: "free",
    draggable: true,
    content: null,
    ...overrides,
  };
};

function healthMessage(runtime: RuntimeState) {
  if (runtime.connection === "connecting") return "Connecting to Charlie runtime.";
  if (runtime.connection === "reconnecting") return "Runtime link interrupted. Reconnecting.";
  if (runtime.connection === "disconnected") return "Charlie runtime is unavailable.";
  return "A Charlie subsystem needs attention.";
}

const issueLabel = (key: string) => ({
  llm: "LLM",
  brain: "LLM",
  voice: "Microphone",
  voice_capture: "Microphone",
  asr: "Speech recognition",
  memory: "Memory",
  web: "Web access",
  browser: "Browser",
  event_bus: "Runtime link",
  frontend: "HUD",
}[key] ?? key.replace(/[_-]+/g, " ").replace(/\b\w/g, value => value.toUpperCase()));

function healthIssues(runtime: RuntimeState): SystemIssue[] {
  if (runtime.connection !== "connected") return [{ label: "Runtime", detail: healthMessage(runtime) }];
  const issues: SystemIssue[] = [];
  const seen = new Set<string>();
  const truthSubsystems = record(runtime.runtimeTruth?.subsystems);
  const healthSubsystems = record(runtime.subsystemHealth.subsystems);
  const sources = [
    truthSubsystems,
    healthSubsystems ?? runtime.subsystemHealth,
  ];
  for (const source of sources) {
    if (!source) continue;
    for (const [key, value] of Object.entries(source)) {
      const details = record(value);
      const status = text(details?.status ?? value).toLowerCase();
      if (!badStatuses.has(status)) continue;
      const label = issueLabel(key);
      if (label === "Speech recognition" && seen.has("Microphone")) continue;
      if (seen.has(label)) continue;
      seen.add(label);
      const detail = text(details?.detail ?? details?.reason ?? details?.message);
      issues.push({ label, detail: detail && !/timestamp|authority|last_success|last_attempt|paerror/i.test(detail) ? detail : "Unavailable" });
    }
  }
  const aggregate = text(runtime.runtimeTruth?.status).toLowerCase();
  if (badStatuses.has(aggregate) && issues.length === 0) issues.push({ label: "Runtime", detail: "Needs attention" });
  const order = ["Runtime", "LLM", "Microphone", "Speech recognition", "Memory", "Web access", "Browser", "HUD"];
  const rank = (label: string) => { const value = order.indexOf(label); return value < 0 ? order.length : value; };
  return issues.sort((a, b) => rank(a.label) - rank(b.label));
}

function presentationContent(presentation: RuntimePresentation, actions: RuntimeActions): ReactNode {
  const content = record(presentation.content);
  const canDismiss = presentation.kind !== "workspace";
  return (
    <div className="runtime-presentation">
      <p>{presentation.summary || text(content?.summary, "Runtime presentation active.")}</p>
      {canDismiss && <button className="text-action runtime-widget__action" type="button"
        onClick={() => actions.dismiss(presentation.id)}>
        Dismiss <span aria-hidden="true">×</span>
      </button>}
    </div>
  );
}

export function mapRuntimeToScene(runtime: RuntimeState, actions: RuntimeActions): RuntimeScene {
  const items: ProjectionData[] = [];
  const used = new Set<string>();
  const add = (item: ProjectionData) => {
    if (!used.has(item.id)) {
      used.add(item.id);
      items.push(item);
    }
  };

  const researchLifecycle = runtime.widgets.research?.lifecycle;
  const researchPayload = normalizeResearchPayload(runtime.research.result);
  const researchStatus = researchPayload?.status.toLowerCase();
  const researchHasMeaningfulResult = Boolean(
    researchPayload
    && !["accepted", "planning", "searching", "error", "timeout", "no_results", "cancelled"].includes(researchStatus || "")
    && (researchPayload.summary.trim() || researchPayload.findings.length > 0 || researchPayload.sources.length > 0),
  );
  const researchActive = researchLifecycle !== "dismissed" &&
    Boolean(runtime.research.progress || runtime.research.result || runtime.research.error);

  for (const approval of Object.values(runtime.approvals)) {
    const sending = Boolean(runtime.inFlightApprovals[approval.requestId]);
    add({
      id: `runtime-approval-${approval.requestId}`,
      importance: 100,
      interactionPriority: 40,
      depth: "priority",
      treatment: "hard",
      label: "Permission required",
      heading: `Allow ${approval.toolName}?`,
      content: <div className="runtime-approval">
        <p>{approval.reason}</p>
        {approval.riskClass && <p className="metadata">Risk // {approval.riskClass}</p>}
        <div className="actions">
          <button disabled={sending} onClick={() => actions.approve(approval.requestId)}>
            {sending ? "Sending…" : "Approve"}
          </button>
          <button disabled={sending} onClick={() => actions.reject(approval.requestId)}>Reject</button>
        </div>
        {runtime.lastError && <p className="runtime-error" role="status">{runtime.lastError}</p>}
      </div>,
    });
  }

  const running = activeTasks(runtime);
  const taskLifecycle = runtime.widgets.tasks?.lifecycle;
  const taskVisible = isWidgetVisible(taskLifecycle) || (running.length > 0 && taskLifecycle !== "dismissed");
  if (taskVisible && !researchActive) {
    const dismiss = widgetDismiss(runtime, "tasks", actions);
    add(widgetProjection(runtime, "tasks", {
      content: <TaskWidget tasks={running} onFocus={actions.focusTask ? actions.focusTask : undefined} onDismiss={dismiss} />,
      depth: "active",
    }));
  }

  const conversationPresentation = widgetPresentation(runtime, "conversation");
  if (!researchActive && isWidgetVisible(runtime.widgets.conversation?.lifecycle) &&
    (conversationPresentation || runtime.conversation.userText || runtime.conversation.responseText || runtime.thinking)) {
    add(widgetProjection(runtime, "conversation", {
      label: runtime.conversation.streaming ? "Conversation / streaming" : "Conversation",
      content: <ChatWidget conversation={runtime.conversation} thinking={runtime.thinking}
        onDismiss={conversationPresentation ? () => { actions.dismiss(conversationPresentation.id); } : undefined}
        onSend={actions.sendChat} />,
    }));
  }

  if (researchActive) {
    const dismiss = widgetDismiss(runtime, "research", actions);
    const compact = !researchHasMeaningfulResult;
    add({
      id: compact ? "runtime-research-progress" : "runtime-research-result",
      importance: compact ? 76 : 98,
      depth: "active",
      treatment: compact ? "free" : "integrated",
      immersive: !compact,
      preferredZone: compact ? "around-core" : "center",
      relatedTo: compact ? "charlie" : undefined,
      draggable: false,
      content: <ResearchWorkspace research={runtime.research} compact={compact} onDismiss={dismiss} onSend={compact ? undefined : actions.sendChat} />,
    });
  }

  const mediaVisible = isWidgetVisible(runtime.widgets.media?.lifecycle);
  if (mediaVisible && runtime.media?.available) {
    const dismiss = widgetDismiss(runtime, "media", actions);
    add(widgetProjection(runtime, "media", {
      content: <MediaWidget media={runtime.media} pending={runtime.mediaPending}
        onControl={actions.mediaControl ? action => { void actions.mediaControl?.(action); } : undefined}
        onDismiss={dismiss} />,
    }));
  }

  if (runtime.lastResult?.kind === "terminal") {
    add({
      id: "runtime-terminal-result",
      importance: 84,
      depth: "active",
      treatment: "hard",
      label: "Terminal / live runtime",
      heading: "Command result",
      content: <div className="runtime-terminal"><pre>{runtime.lastResult.summary}</pre>
        {runtime.lastResult.detail && <p className="metadata">{runtime.lastResult.detail}</p>}</div>,
      immersive: true,
      preferredZone: "center",
    });
  } else if (runtime.lastResult?.kind === "research" && !runtime.research.result && !researchActive) {
    add({
      id: "runtime-research-result",
      importance: 72,
      depth: "active",
      treatment: "free",
      label: "Research / live runtime",
      heading: runtime.lastResult.title,
      content: <p className="runtime-result">{runtime.lastResult.summary}</p>,
      draggable: true,
      relatedTo: "charlie",
      preferredZone: "around-core",
    });
  } else if (runtime.lastAlert && !researchActive) {
    add({
      id: "runtime-alert",
      importance: 78,
      depth: "priority",
      treatment: "free",
      label: "Runtime alert",
      heading: "Attention required",
      content: <p className="runtime-result">{runtime.lastAlert}</p>,
      draggable: true,
      relatedTo: "charlie",
      preferredZone: "around-core",
    });
  }

  const progress = runtime.research.progress;
  if (progress && !researchActive) {
    add({
      id: "runtime-research-progress",
      importance: 72,
      depth: "active",
      treatment: "free",
      label: "Research / live runtime",
      heading: progress.stage,
      content: <div className="runtime-progress"><p>{progress.message}</p>
        {progress.current !== undefined && progress.total !== undefined &&
          <p className="metadata">Reading source {progress.current} / {progress.total}</p>}
      </div>,
    });
  }

  if (runtime.activeActivity && runtime.activeActivity.kind !== "task" &&
    !(runtime.activeActivity.kind === "research" && progress)) {
    const activity = runtime.activeActivity;
    add({
      id: "runtime-activity",
      importance: activity.kind === "research" ? 74 : 68,
      depth: "active",
      treatment: activity.kind === "terminal" ? "hard" : "free",
      label: `${activity.kind} / live runtime`,
      heading: activity.label,
      content: <div className="runtime-activity"><p>{activity.detail || "In progress."}</p></div>,
      immersive: activity.kind === "terminal",
      preferredZone: activity.kind === "terminal" ? "center" : "around-core",
      relatedTo: activity.kind === "terminal" ? undefined : "charlie",
      draggable: activity.kind !== "terminal",
    });
  }

  const issues = healthIssues(runtime);
  const systemPresentation = widgetPresentation(runtime, "system");
  const systemLifecycle = runtime.widgets.system?.lifecycle;
  if (systemPresentation && isWidgetVisible(systemLifecycle)) {
    const dismiss = widgetDismiss(runtime, "system", actions);
    add(widgetProjection(runtime, "system", {
      importance: 72,
      depth: "active",
      content: <SystemWidget issues={issues.length ? issues : [{ label: "Runtime", detail: "Connected" }]} onDismiss={dismiss} />,
    }));
  }

  for (const presentation of Object.values(runtime.presentations)
    .sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0))) {
    if (presentation.kind === "attention" && runtime.approvals[presentation.id]) continue;
    const widgetId = inferWidgetId(presentation);
    if (widgetId === "conversation" || widgetId === "tasks" || widgetId === "research" ||
      widgetId === "media" || widgetId === "system") continue;
    const immersive = presentation.kind === "workspace";
    add({
      id: `runtime-presentation-${presentation.id}`,
      importance: Math.max(30, Math.min(80, presentation.priority ?? 45)),
      depth: presentation.kind === "notification" ? "background" : "active",
      treatment: immersive ? "structured" : "free",
      label: `${presentation.kind} / live runtime`,
      heading: presentation.title,
      content: presentationContent(presentation, actions),
      immersive,
      preferredZone: immersive ? "center" : "around-core",
      relatedTo: immersive ? undefined : "charlie",
      draggable: !immersive,
    });
  }

  if (runtime.lastError && Object.keys(runtime.approvals).length === 0) {
    add({
      id: "runtime-command-error",
      importance: 76,
      depth: "priority",
      treatment: "free",
      label: "Runtime command",
      heading: "Action not sent",
      content: <p className="runtime-error" role="status">{runtime.lastError}</p>,
      draggable: true,
      relatedTo: "charlie",
      preferredZone: "around-core",
    });
  }

  const state = runtime.connection === "connected" ? runtime.coreState : "degraded";
  return { items, state };
}
