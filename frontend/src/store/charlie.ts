import { create } from "zustand";
import type { WSEvent } from "../runtime/bridge";

export interface SurfaceAction {
  id: string;
  label: string;
  style: string;
}

export interface SurfaceSpec {
  surfaceId: string;
  presentation: "background" | "notification" | "widget" | "floating" | "modal" | "workspace";
  persistence: "ephemeral" | "persistent" | "archived";
  density: number;
  region: string;
  taskId: string | null;
  rationale: string;
  ttlSeconds: number | null;
  title: string;
  body: string;
  role: string;
  rect: [number, number, number, number] | null;
  actions: SurfaceAction[];
  kind: string;
}

export interface SystemStatus {
  cpu: number | null;
  ram: number | null;
  gpu: number | null;
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
}

export interface AudioState {
  muted: boolean;
  volume: number;
}

type SurfaceMap = Record<string, SurfaceSpec>;

// One primitive per selector (CLAUDE.md sec8.5) -- components read a single field, never the whole store.
interface CharlieState {
  connected: boolean;
  coreState: string;
  activities: string[];
  widgets: SurfaceMap;
  modals: SurfaceMap;
  workspaces: SurfaceMap;
  notifications: SurfaceMap;
  activeToolApproval: ToolApprovalRequest | null;
  systemStatus: SystemStatus | null;
  netHistory: number[];
  subsystemHealth: Record<string, SubsystemHealth>;
  tasks: Record<string, RuntimeTask>;
  mcpStatus: Record<string, McpServerStatus>;
  chatMessages: ChatMessage[];
  activeAlert: AlertInfo | null;
  audioState: AudioState | null;
  micMuted: boolean | null;
  setConnected: (connected: boolean) => void;
  setActiveToolApproval: (req: ToolApprovalRequest | null) => void;
  seedMcpStatus: (servers: Record<string, boolean>) => void;
  addUserMessage: (text: string) => void;
  setChatMessages: (messages: ChatMessage[]) => void;
  dismissAlert: () => void;
  applyEvent: (event: WSEvent) => void;
}

function surfaceMapKey(presentation: string): "widgets" | "modals" | "workspaces" | "notifications" | null {
  switch (presentation) {
    case "widget":
    case "floating":
      return "widgets";
    case "modal":
      return "modals";
    case "workspace":
      return "workspaces";
    case "notification":
      return "notifications";
    default:
      return null;
  }
}

function specFromPayload(payload: Record<string, unknown>): SurfaceSpec {
  return {
    surfaceId: String(payload.surface_id),
    presentation: payload.presentation as SurfaceSpec["presentation"],
    persistence: payload.persistence as SurfaceSpec["persistence"],
    density: Number(payload.density ?? 0),
    region: String(payload.region ?? ""),
    taskId: (payload.task_id as string) ?? null,
    rationale: String(payload.rationale ?? ""),
    ttlSeconds: (payload.ttl_seconds as number) ?? null,
    title: String(payload.title ?? ""),
    body: String(payload.body ?? ""),
    role: String(payload.role ?? "info"),
    rect: (payload.rect as [number, number, number, number]) ?? null,
    actions: (payload.actions as SurfaceAction[]) ?? [],
    kind: String(payload.kind ?? "generic"),
  };
}

export const useCharlieStore = create<CharlieState>((set) => ({
  connected: false,
  coreState: "idle",
  activities: [],
  widgets: {},
  modals: {},
  workspaces: {},
  notifications: {},
  activeToolApproval: null,
  systemStatus: null,
  netHistory: [],
  subsystemHealth: {},
  tasks: {},
  mcpStatus: {},
  chatMessages: [],
  activeAlert: null,
  audioState: null,
  micMuted: null,

  setConnected: (connected) => set({ connected }),
  dismissAlert: () => set({ activeAlert: null }),
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
      case "surface_spawn":
      case "surface_update":
        applySurfaceUpsert(set, payload);
        return;
      case "surface_dismiss":
        applySurfaceDismiss(set, payload);
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
  return {
    id,
    title: String(task.title ?? ""),
    status: String(task.status ?? "unknown"),
    currentStep: Number(task.current_step ?? 0),
    totalSteps: Number(task.total_steps ?? 0),
  };
}

function subsystemHealthFromPayload(payload: Record<string, unknown>): Record<string, SubsystemHealth> {
  return Object.entries(payload).reduce<Record<string, SubsystemHealth>>((health, [name, raw]) => {
    if (!raw || typeof raw !== "object") return health;
    const value = raw as Record<string, unknown>;
    health[name] = { status: String(value.status ?? "unknown"), detail: String(value.detail ?? "Unknown") };
    return health;
  }, {});
}

function applySurfaceUpsert(set: (fn: (s: CharlieState) => Partial<CharlieState>) => void, payload: Record<string, unknown>): void {
  const key = surfaceMapKey(String(payload.presentation));
  if (!key) return;
  const spec = specFromPayload(payload);
  set((s) => ({ [key]: { ...s[key], [spec.surfaceId]: spec } }) as Partial<CharlieState>);
}

function applySurfaceDismiss(set: (fn: (s: CharlieState) => Partial<CharlieState>) => void, payload: Record<string, unknown>): void {
  const surfaceId = String(payload.surface_id);
  set((s) => {
    const next: Partial<CharlieState> = {};
    for (const key of ["widgets", "modals", "workspaces", "notifications"] as const) {
      if (surfaceId in s[key]) {
        const { [surfaceId]: _removed, ...rest } = s[key];
        next[key] = rest;
      }
    }
    return next;
  });
}
