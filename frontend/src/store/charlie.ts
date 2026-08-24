import { create } from "zustand";
import type { WSEvent } from "../runtime/bridge";
import { useWorkspaceStore } from "../layout/workspaceStore";
import { useWidgetStore } from "../layout/widgetStore";

export interface SystemStatus {
  cpu: number | null;
  ram: number | null;
  gpu: number | null;
  disk: number | null;
  netKbps: number | null;
  uptimeSeconds: number | null;
  batteryPercent: number | null;
}

export interface McpServerStatus {
  status: string;
  toolCount: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "charlie";
  text: string;
  pending: boolean;
}

export interface ResearchResultPayload {
  query: string;
  mode: string;
  confidence: number;
  stop_reason: string;
  citations: Array<Record<string, unknown>>;
  sources: Array<Record<string, unknown>>;
  products: Array<Record<string, unknown>>;
  media: Array<Record<string, unknown>>;
}

export interface ToolApprovalRequest {
  request_id: string;
  tool_name: string;
  reason: string;
  arguments: Record<string, unknown>;
  risk_class: string | null;
}

export interface AlertInfo {
  id: string;
  severity: string;
  message: string;
}

export interface SubsystemHealth {
  status: string;
  detail: string;
}

export interface RuntimeTask {
  id: string;
  title: string;
  status: string;
  currentStep: number;
  totalSteps: number;
  origin?: string;
  priority?: string;
  sessionId?: string;
  parentTaskId?: string;
  progress?: number | null;
  currentAction?: string;
  waitingReason?: string;
  resultReference?: string;
  approvalReference?: string;
  capabilityRequirements?: string[];
}

export interface PresentationIntent {
  id: string;
  kind: "silent" | "caption" | "notification" | "widget" | "composed_surface" | "workspace" | "overlay" | "attention";
  sourceEventId?: string;
  taskId?: string | null;
  sessionId?: string | null;
  capability?: string | null;
  operation?: string | null;
  title: string;
  summary: string;
  content: Record<string, unknown>;
  priority: number;
  attentionLevel: "none" | "low" | "normal" | "high" | "critical";
  dismissPolicy: "immediate" | "timed" | "manual" | "persistent" | "task_lifetime";
  autoDismissMs?: number | null;
  workspaceType?: string | null;
  widgetType?: string | null;
  overlayType?: string | null;
  surfaceSpec?: Record<string, unknown> | null;
  preferredZone: "contextual" | "top_right" | "bottom_right" | "top_left" | "bottom_left" | "center";
  anchor: "core" | "workspace" | "screen" | "widget";
  spokenText?: string | null;
  captionText?: string | null;
  createdAt: string;
  expiresAt?: string | null;
  replaceKey?: string | null;
  correlationId?: string | null;
  replayable: boolean;
}

export interface AudioState {
  muted: boolean;
  volume: number;
}

// One primitive per selector (CLAUDE.md sec8.5) -- components read a single field, never the whole store.
interface CharlieState {
  connected: boolean;
  coreState: string;
  activities: string[];
  presentationIntents: Record<string, PresentationIntent>;
  activeCaption: string | null;
  activeToolApproval: ToolApprovalRequest | null;
  systemStatus: SystemStatus | null;
  netHistory: number[];
  subsystemHealth: Record<string, SubsystemHealth>;
  tasks: Record<string, RuntimeTask>;
  mcpStatus: Record<string, McpServerStatus>;
  chatMessages: ChatMessage[];
  latestResearchResult: ResearchResultPayload | null;
  activeAlert: AlertInfo | null;
  audioState: AudioState | null;
  micMuted: boolean | null;
  audioLevel: number;
  hudVisible: boolean;
  setConnected: (connected: boolean) => void;
  setActiveToolApproval: (req: ToolApprovalRequest | null) => void;
  seedMcpStatus: (servers: Record<string, boolean>) => void;
  addUserMessage: (text: string) => void;
  setChatMessages: (messages: ChatMessage[]) => void;
  dismissAlert: () => void;
  dismissPresentationIntent: (id: string) => void;
  applyEvent: (event: WSEvent) => void;
}

export const useCharlieStore = create<CharlieState>((set) => ({
  connected: false,
  coreState: "idle",
  activities: [],
  presentationIntents: {},
  activeCaption: null,
  activeToolApproval: null,
  systemStatus: null,
  netHistory: [],
  subsystemHealth: {},
  tasks: {},
  mcpStatus: {},
  chatMessages: [],
  latestResearchResult: null,
  activeAlert: null,
  audioState: null,
  micMuted: null,
  audioLevel: 0,
  hudVisible: true,

  setConnected: (connected) => set({ connected }),
  dismissAlert: () => set({ activeAlert: null }),
  dismissPresentationIntent: (id: string) => {
    useWorkspaceStore.getState().closeWorkspace(id);
    useWidgetStore.getState().dismissWidget(id);
    set((s) => {
      if (!(id in s.presentationIntents)) return {};
      const { [id]: _removed, ...rest } = s.presentationIntents;
      return { presentationIntents: rest };
    });
  },
  setActiveToolApproval: (activeToolApproval) => set({ activeToolApproval }),
  seedMcpStatus: (servers) =>
    set((s) => {
      const next = { ...s.mcpStatus };
      for (const [name, running] of Object.entries(servers)) {
        if (!(name in next)) next[name] = { status: running ? "connected" : "stopped", toolCount: 0 };
      }
      return { mcpStatus: next };
    }),
  addUserMessage: (text) =>
    set((s) => ({
      chatMessages: [...s.chatMessages, { id: `${Date.now()}`, role: "user", text, pending: false }],
    })),
  setChatMessages: (chatMessages) => set({ chatMessages }),

  applyEvent: (event) => {
    const payload = event.payload ?? {};
    switch (event.type) {
      case "charlie_state":
        set({
          coreState: String(payload.state ?? "idle"),
          activities: (payload.activities as string[]) ?? [],
        });
        return;
      case "tool_approval_request":
        set({ activeToolApproval: payload as unknown as ToolApprovalRequest });
        return;
      case "tool_approval_resolved":
        set((s) =>
          s.activeToolApproval?.request_id === payload.request_id ? { activeToolApproval: null } : {}
        );
        return;
      case "system_status": {
        const netKbps = numberOrNull(payload.net_kbps);
        set((s) => ({
          systemStatus: {
            cpu: numberOrNull(payload.cpu),
            ram: numberOrNull(payload.ram),
            gpu: numberOrNull(payload.gpu),
            disk: numberOrNull(payload.disk_percent),
            netKbps,
            uptimeSeconds: numberOrNull(payload.uptime_seconds),
            batteryPercent: numberOrNull(payload.battery_percent),
          },
          netHistory: netKbps === null ? s.netHistory : [...s.netHistory.slice(-23), netKbps],
        }));
        return;
      }
      case "subsystem_health":
        set({ subsystemHealth: subsystemHealthFromPayload(payload) });
        return;
      case "task_snapshot":
        set({ tasks: taskMapFromPayload(payload.tasks) });
        return;
      case "background_task": {
        const task = taskFromPayload(payload);
        if (task) set((s) => ({ tasks: { ...s.tasks, [task.id]: task } }));
        return;
      }
      case "alert":
        set({
          activeAlert: {
            id: `${Date.now()}`,
            severity: String(payload.severity ?? "info"),
            message: String(payload.message ?? ""),
          },
        });
        return;
      case "mcp_status_changed":
        set((s) => ({
          mcpStatus: {
            ...s.mcpStatus,
            [String(payload.server_name)]: {
              status: String(payload.status ?? "unknown"),
              toolCount: Number(payload.tool_count ?? 0),
            },
          },
        }));
        return;
      case "audio_state":
        set({
          audioState: {
            muted: Boolean(payload.muted),
            volume: numberOrNull(payload.volume) ?? 0,
          },
        });
        return;
      case "mic_state":
        set({ micMuted: typeof payload.mic_muted === "boolean" ? payload.mic_muted : null });
        return;
      case "audio_level": {
        const level = numberOrNull(payload.level);
        set({ audioLevel: level === null ? 0 : Math.max(0, Math.min(1, level)) });
        return;
      }
      case "hud_visibility":
        set({ hudVisible: Boolean(payload.visible) });
        return;
      case "token":
        set((s) => {
          const text = String(payload.text ?? "");
          const last = s.chatMessages[s.chatMessages.length - 1];
          if (last && last.role === "charlie" && last.pending) {
            const updated = { ...last, text: last.text + text };
            return { chatMessages: [...s.chatMessages.slice(0, -1), updated] };
          }
          return {
            chatMessages: [...s.chatMessages, { id: `${Date.now()}`, role: "charlie", text, pending: true }],
          };
        });
        return;
      case "response_done":
        set((s) => {
          const last = s.chatMessages[s.chatMessages.length - 1];
          if (!last || last.role !== "charlie" || !last.pending) return {};
          return { chatMessages: [...s.chatMessages.slice(0, -1), { ...last, pending: false }] };
        });
        return;
      case "research_result":
        set({ latestResearchResult: payload as unknown as ResearchResultPayload });
        return;
      case "presentation_intent":
      case "presentation_update":
        applyPresentationIntentUpsert(set, payload);
        return;
      case "presentation_dismiss":
        applyPresentationIntentDismiss(set, payload);
        return;
      case "presentation_command":
        if (payload.action === "clear_screen") {
          useWidgetStore.getState().clearScreen();
          useWorkspaceStore.getState().clearWorkspaces();
          set((s) => ({
            presentationIntents: Object.fromEntries(
              Object.entries(s.presentationIntents).filter(([, intent]) => intent.kind === "attention"),
            ),
          }));
        }
        if (payload.action === "focus_task" && typeof payload.task_id === "string") {
          useWorkspaceStore.getState().focusWorkspaceForTask(payload.task_id);
        }
        return;
      default:
        return;
    }
  },
}));

function taskMapFromPayload(rawTasks: unknown): Record<string, RuntimeTask> {
  if (!Array.isArray(rawTasks)) return {};
  return rawTasks.reduce<Record<string, RuntimeTask>>((tasks, rawTask) => {
    const task = taskFromPayload(rawTask);
    if (task) tasks[task.id] = task;
    return tasks;
  }, {});
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function taskFromPayload(rawTask: unknown): RuntimeTask | null {
  if (!rawTask || typeof rawTask !== "object") return null;
  const task = rawTask as Record<string, unknown>;
  const id = String(task.id ?? "");
  if (!id) return null;
  const status = normalizeTaskStatus(task.status);
  const runtimeTask: RuntimeTask = {
    id,
    title: String(task.title ?? ""),
    status,
    currentStep: Number(task.current_step ?? 0),
    totalSteps: Number(task.total_steps ?? 0),
  };
  if (typeof task.origin === "string") runtimeTask.origin = task.origin;
  if (typeof task.priority === "string") runtimeTask.priority = task.priority;
  if (typeof task.session_id === "string") runtimeTask.sessionId = task.session_id;
  if (typeof task.parent_task_id === "string") runtimeTask.parentTaskId = task.parent_task_id;
  if (typeof task.progress === "number" || task.progress === null) runtimeTask.progress = task.progress as number | null;
  if (typeof task.current_action === "string") runtimeTask.currentAction = task.current_action;
  if (typeof task.waiting_reason === "string") runtimeTask.waitingReason = task.waiting_reason;
  if (typeof task.result_reference === "string") runtimeTask.resultReference = task.result_reference;
  if (typeof task.approval_reference === "string") runtimeTask.approvalReference = task.approval_reference;
  if (Array.isArray(task.capability_requirements)) {
    runtimeTask.capabilityRequirements = task.capability_requirements.filter((value): value is string => typeof value === "string");
  }
  return runtimeTask;
}

function normalizeTaskStatus(status: unknown): string {
  switch (status) {
    case "done":
      return "completed";
    case "awaiting_approval":
      return "approval_required";
    default:
      return String(status ?? "unknown");
  }
}

function subsystemHealthFromPayload(payload: Record<string, unknown>): Record<string, SubsystemHealth> {
  return Object.entries(payload).reduce<Record<string, SubsystemHealth>>((health, [name, raw]) => {
    if (!raw || typeof raw !== "object") return health;
    const value = raw as Record<string, unknown>;
    health[name] = { status: String(value.status ?? "unknown"), detail: String(value.detail ?? "Unknown") };
    return health;
  }, {});
}

function presentationIntentFromPayload(payload: Record<string, unknown>): PresentationIntent {
  return {
    id: String(payload.id ?? ""),
    kind: (payload.kind as PresentationIntent["kind"]) ?? "silent",
    sourceEventId: (typeof payload.source_event_id === "string" ? payload.source_event_id : payload.sourceEventId) as string | undefined,
    taskId: (payload.task_id as string) ?? (payload.taskId as string) ?? null,
    sessionId: (payload.session_id as string) ?? (payload.sessionId as string) ?? null,
    capability: (payload.capability as string) ?? null,
    operation: (payload.operation as string) ?? null,
    title: String(payload.title ?? ""),
    summary: String(payload.summary ?? ""),
    content: (payload.content as Record<string, unknown>) ?? {},
    priority: Number(payload.priority ?? 50),
    attentionLevel: (payload.attention_level as PresentationIntent["attentionLevel"]) ?? (payload.attentionLevel as PresentationIntent["attentionLevel"]) ?? "normal",
    dismissPolicy: (payload.dismiss_policy as PresentationIntent["dismissPolicy"]) ?? (payload.dismissPolicy as PresentationIntent["dismissPolicy"]) ?? "timed",
    autoDismissMs: typeof payload.auto_dismiss_ms === "number" ? payload.auto_dismiss_ms : typeof payload.autoDismissMs === "number" ? payload.autoDismissMs : null,
    workspaceType: (payload.workspace_type as string) ?? (payload.workspaceType as string) ?? null,
    widgetType: (payload.widget_type as string) ?? (payload.widgetType as string) ?? null,
    overlayType: (payload.overlay_type as string) ?? (payload.overlayType as string) ?? null,
    surfaceSpec: (payload.surface_spec as Record<string, unknown>) ?? (payload.surfaceSpec as Record<string, unknown>) ?? null,
    preferredZone: (payload.preferred_zone as PresentationIntent["preferredZone"]) ?? (payload.preferredZone as PresentationIntent["preferredZone"]) ?? "contextual",
    anchor: (payload.anchor as PresentationIntent["anchor"]) ?? "core",
    spokenText: (payload.spoken_text as string) ?? (payload.spokenText as string) ?? null,
    captionText: (payload.caption_text as string) ?? (payload.captionText as string) ?? null,
    createdAt: String(payload.created_at ?? payload.createdAt ?? new Date().toISOString()),
    expiresAt: (payload.expires_at as string) ?? (payload.expiresAt as string) ?? null,
    replaceKey: (payload.replace_key as string) ?? (payload.replaceKey as string) ?? null,
    correlationId: (payload.correlation_id as string) ?? (payload.correlationId as string) ?? null,
    replayable: Boolean(payload.replayable ?? false),
  };
}

function applyPresentationIntentUpsert(
  set: (fn: (s: CharlieState) => Partial<CharlieState>) => void,
  payload: Record<string, unknown>
): void {
  const intent = presentationIntentFromPayload(payload);
  if (!intent.id) return;
  set((s) => {
    const nextIntents = { ...s.presentationIntents };
    if (intent.replaceKey) {
      for (const [id, item] of Object.entries(nextIntents)) {
        if (item.replaceKey === intent.replaceKey && id !== intent.id) {
          delete nextIntents[id];
        }
      }
    }
    nextIntents[intent.id] = intent;
    const caption = intent.kind === "caption" ? intent.captionText || intent.summary : s.activeCaption;
    return { presentationIntents: nextIntents, activeCaption: caption };
  });
}

function applyPresentationIntentDismiss(
  set: (fn: (s: CharlieState) => Partial<CharlieState>) => void,
  payload: Record<string, unknown>
): void {
  const id = String(payload.id ?? "");
  if (!id) return;
  useWorkspaceStore.getState().closeWorkspace(id);
  useWidgetStore.getState().dismissWidget(id);
  set((s) => {
    if (!(id in s.presentationIntents)) return {};
    const { [id]: _removed, ...rest } = s.presentationIntents;
    return { presentationIntents: rest };
  });
}

if (typeof window !== "undefined") {
  (window as unknown as Record<string, unknown>).__CHARLIE_STORE__ = useCharlieStore;
  (window as unknown as Record<string, unknown>).__WORKSPACE_STORE__ = useWorkspaceStore;
  (window as unknown as Record<string, unknown>).__WIDGET_STORE__ = useWidgetStore;
}

