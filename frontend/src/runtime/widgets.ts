export type WidgetLifecycle = "hidden" | "summoned" | "compact" | "expanded" | "collapsed" | "dismissed";
export type WidgetId = "conversation" | "media" | "tasks" | "research" | "resources" | "system";
export type WidgetZone = "center" | "around-core" | "bottom-right";

export interface WidgetDefinition {
  id: WidgetId;
  source: string;
  priority: number;
  preferredFootprint: "compact" | "medium" | "large";
  preferredZone: WidgetZone;
  expandable: boolean;
  immersiveWhenExpanded: boolean;
  autoShowTrigger: "explicit" | "active-task" | "research-progress" | "degraded-health";
  autoDismiss: "explicit" | "task-completion" | "short";
  supportedActions: readonly string[];
}

export const widgetRegistry: Record<WidgetId, WidgetDefinition> = {
  conversation: {
    id: "conversation", source: "runtime.conversation", priority: 86,
    preferredFootprint: "medium", preferredZone: "around-core", expandable: true,
    immersiveWhenExpanded: false, autoShowTrigger: "explicit", autoDismiss: "explicit",
    supportedActions: ["open", "close", "expand"],
  },
  media: {
    id: "media", source: "runtime.media", priority: 74,
    preferredFootprint: "compact", preferredZone: "around-core", expandable: true,
    immersiveWhenExpanded: false, autoShowTrigger: "explicit", autoDismiss: "explicit",
    supportedActions: ["play_pause", "next_track", "previous_track", "volume", "close"],
  },
  tasks: {
    id: "tasks", source: "runtime.tasks", priority: 62,
    preferredFootprint: "compact", preferredZone: "around-core", expandable: true,
    immersiveWhenExpanded: true, autoShowTrigger: "active-task", autoDismiss: "task-completion",
    supportedActions: ["focus", "close", "expand"],
  },
  research: {
    id: "research", source: "runtime.research", priority: 72,
    preferredFootprint: "medium", preferredZone: "around-core", expandable: true,
    immersiveWhenExpanded: true, autoShowTrigger: "research-progress", autoDismiss: "task-completion",
    supportedActions: ["expand", "close"],
  },
  resources: {
    id: "resources", source: "runtime.presentations.resources", priority: 45,
    preferredFootprint: "compact", preferredZone: "around-core", expandable: true,
    immersiveWhenExpanded: true, autoShowTrigger: "explicit", autoDismiss: "explicit",
    supportedActions: ["open", "close", "expand"],
  },
  system: {
    id: "system", source: "runtime.runtimeTruth", priority: 24,
    preferredFootprint: "compact", preferredZone: "around-core", expandable: true,
    immersiveWhenExpanded: false, autoShowTrigger: "degraded-health", autoDismiss: "short",
    supportedActions: ["open", "close"],
  },
};

export interface RuntimeWidgetState {
  lifecycle: WidgetLifecycle;
  sourceId?: string;
}

export function getWidgetDefinition(id: string): WidgetDefinition | undefined {
  return widgetRegistry[id as WidgetId];
}

export function isWidgetVisible(lifecycle: WidgetLifecycle | undefined): boolean {
  return lifecycle === "summoned" || lifecycle === "compact" || lifecycle === "expanded";
}

export function isWidgetExpanded(lifecycle: WidgetLifecycle | undefined): boolean {
  return lifecycle === "expanded";
}

export function inferWidgetId(value: unknown): WidgetId | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const item = value as Record<string, unknown>;
  const identity = [item.id, item.widget_id, item.widgetId, item.workspace_type, item.workspaceType,
    item.widget_type, item.widgetType].filter(value => typeof value === "string").join(" ").toLowerCase();
  const descriptive = [item.title, item.kind].filter(value => typeof value === "string").join(" ").toLowerCase();
  if (item.kind === "notification" && !item.widget_type && !item.widgetType &&
    !item.workspace_type && !item.workspaceType) return undefined;
  const haystack = `${identity} ${item.kind === "workspace" || item.kind === "widget" ? descriptive : ""}`;
  if (/conversation|chat/.test(haystack)) return "conversation";
  if (/music|media|track|player/.test(haystack)) return "media";
  if (/task|background/.test(haystack)) return "tasks";
  if (/resource|source/.test(haystack)) return "resources";
  if (/research|finding/.test(haystack)) return "research";
  if (/system|health|diagnos/.test(haystack)) return "system";
  return undefined;
}
