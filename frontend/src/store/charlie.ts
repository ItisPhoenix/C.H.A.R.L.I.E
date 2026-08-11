import { create } from "zustand";
import type { WSEvent } from "../runtime/bridge";

export interface SurfaceSpec {
  surfaceId: string;
  presentation: "background" | "notification" | "widget" | "floating" | "modal" | "workspace";
  persistence: "ephemeral" | "persistent" | "archived";
  density: number;
  region: string;
  taskId: string | null;
  rationale: string;
  ttlSeconds: number | null;
}

export interface ToolApprovalRequest {
  request_id: string;
  tool_name: string;
  reason: string;
  arguments: Record<string, unknown>;
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
  setConnected: (connected: boolean) => void;
  setActiveToolApproval: (req: ToolApprovalRequest | null) => void;
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

  setConnected: (connected) => set({ connected }),
  setActiveToolApproval: (activeToolApproval) => set({ activeToolApproval }),

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
