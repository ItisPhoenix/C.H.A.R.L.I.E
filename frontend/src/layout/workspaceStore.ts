import { create } from "zustand";
import type { PresentationIntent } from "../store/charlie";

export type WorkspaceLifecycleState = "opening" | "active" | "minimized" | "closing" | "closed";

export interface WorkspaceInstance {
  id: string;
  type: string;
  presentationIntentId: string;
  taskId: string | null;
  title: string;
  summary: string;
  status: string;
  lifecycleState: WorkspaceLifecycleState;
  focused: boolean;
  openedAt: string;
  lastFocusedAt: string;
  persistent: boolean;
  replayable: boolean;
  contentState: Record<string, unknown>;
}

export interface RecentWorkspaceEntry {
  id: string;
  type: string;
  title: string;
  summary: string;
  taskId: string | null;
  closedAt: string;
  contentState: Record<string, unknown>;
}

interface WorkspaceStoreState {
  workspaces: Record<string, WorkspaceInstance>;
  activeWorkspaceId: string | null;
  recentWorkspaces: RecentWorkspaceEntry[];

  // Actions
  openWorkspace: (intent: PresentationIntent) => WorkspaceInstance;
  focusWorkspace: (id: string) => void;
  focusWorkspaceForTask: (taskId: string) => void;
  minimizeWorkspace: (id: string) => void;
  restoreWorkspace: (id: string) => void;
  closeWorkspace: (id: string) => void;
  clearWorkspaces: () => void;
  getActiveWorkspace: () => WorkspaceInstance | null;
}

export const useWorkspaceStore = create<WorkspaceStoreState>((set, get) => ({
  workspaces: {},
  activeWorkspaceId: null,
  recentWorkspaces: [],

  openWorkspace: (intent: PresentationIntent) => {
    const now = new Date().toISOString();
    const currentActiveId = get().activeWorkspaceId;

    if (currentActiveId === intent.id && get().workspaces[intent.id]?.lifecycleState === "active") {
      return get().workspaces[intent.id]!;
    }

    // If there's an existing active workspace, minimize it into Recent without destroying state
    if (currentActiveId && currentActiveId !== intent.id) {
      const current = get().workspaces[currentActiveId];
      if (current) {
        set((state) => ({
          workspaces: {
            ...state.workspaces,
            [currentActiveId]: { ...current, lifecycleState: "minimized", focused: false },
          },
          recentWorkspaces: [
            {
              id: current.id,
              type: current.type,
              title: current.title,
              summary: current.summary,
              taskId: current.taskId,
              closedAt: now,
              contentState: current.contentState,
            },
            ...state.recentWorkspaces.filter((r) => r.id !== current.id).slice(0, 9),
          ],
        }));
      }
    }

    const resolvedType = (
      intent.workspaceType ||
      (intent as any).workspace_type ||
      (intent as any).type ||
      "custom"
    ).toLowerCase();

    const resolvedContent = (intent.content || (intent as any).contentState || {}) as Record<string, unknown>;

    const newInstance: WorkspaceInstance = {
      id: intent.id,
      type: resolvedType,
      presentationIntentId: intent.id,
      taskId: intent.taskId ?? null,
      title: intent.title || `WORKSPACE // ${resolvedType.toUpperCase()}`,
      summary: intent.summary || "",
      status: "active",
      lifecycleState: "active",
      focused: true,
      openedAt: now,
      lastFocusedAt: now,
      persistent: intent.dismissPolicy === "persistent",
      replayable: intent.replayable,
      contentState: resolvedContent,
    };

    set((state) => ({
      workspaces: {
        ...state.workspaces,
        [intent.id]: newInstance,
      },
      activeWorkspaceId: intent.id,
    }));

    return newInstance;
  },

  focusWorkspace: (id: string) => {
    const ws = get().workspaces[id];
    if (!ws) return;
    set((state) => ({
      workspaces: {
        ...state.workspaces,
        [id]: { ...ws, focused: true, lastFocusedAt: new Date().toISOString() },
      },
      activeWorkspaceId: id,
    }));
  },

  focusWorkspaceForTask: (taskId: string) => {
    const workspace = Object.values(get().workspaces).find((item) => item.taskId === taskId);
    if (workspace) get().focusWorkspace(workspace.id);
  },

  minimizeWorkspace: (id: string) => {
    const ws = get().workspaces[id];
    if (!ws) return;
    const now = new Date().toISOString();

    set((state) => ({
      workspaces: {
        ...state.workspaces,
        [id]: { ...ws, lifecycleState: "minimized", focused: false },
      },
      activeWorkspaceId: state.activeWorkspaceId === id ? null : state.activeWorkspaceId,
      recentWorkspaces: [
        {
          id: ws.id,
          type: ws.type,
          title: ws.title,
          summary: ws.summary,
          taskId: ws.taskId,
          closedAt: now,
          contentState: ws.contentState,
        },
        ...state.recentWorkspaces.filter((r) => r.id !== ws.id).slice(0, 9),
      ],
    }));
  },

  restoreWorkspace: (id: string) => {
    const ws = get().workspaces[id];
    const currentActiveId = get().activeWorkspaceId;

    // If another workspace is active, minimize it
    if (currentActiveId && currentActiveId !== id) {
      const current = get().workspaces[currentActiveId];
      if (current) {
        set((state) => ({
          workspaces: {
            ...state.workspaces,
            [currentActiveId]: { ...current, lifecycleState: "minimized", focused: false },
          },
        }));
      }
    }

    if (ws) {
      set((state) => ({
        workspaces: {
          ...state.workspaces,
          [id]: { ...ws, lifecycleState: "active", focused: true, lastFocusedAt: new Date().toISOString() },
        },
        activeWorkspaceId: id,
      }));
    } else {
      // Restore from recent list if available
      const recent = get().recentWorkspaces.find((r) => r.id === id);
      if (recent) {
        const restored: WorkspaceInstance = {
          id: recent.id,
          type: recent.type,
          presentationIntentId: recent.id,
          taskId: recent.taskId,
          title: recent.title,
          summary: recent.summary,
          status: "active",
          lifecycleState: "active",
          focused: true,
          openedAt: new Date().toISOString(),
          lastFocusedAt: new Date().toISOString(),
          persistent: true,
          replayable: true,
          contentState: recent.contentState,
        };
        set((state) => ({
          workspaces: {
            ...state.workspaces,
            [id]: restored,
          },
          activeWorkspaceId: id,
        }));
      }
    }
  },

  closeWorkspace: (id: string) => {
    const ws = get().workspaces[id];
    const now = new Date().toISOString();

    set((state) => {
      const nextWorkspaces = { ...state.workspaces };
      delete nextWorkspaces[id];

      const nextRecent = ws
        ? [
            {
              id: ws.id,
              type: ws.type,
              title: ws.title,
              summary: ws.summary,
              taskId: ws.taskId,
              closedAt: now,
              contentState: ws.contentState,
            },
            ...state.recentWorkspaces.filter((r) => r.id !== id).slice(0, 9),
          ]
        : state.recentWorkspaces;

      return {
        workspaces: nextWorkspaces,
        activeWorkspaceId: state.activeWorkspaceId === id ? null : state.activeWorkspaceId,
        recentWorkspaces: nextRecent,
      };
    });
  },

  clearWorkspaces: () => {
    const activeId = get().activeWorkspaceId;
    if (activeId) {
      get().minimizeWorkspace(activeId);
    }
  },

  getActiveWorkspace: () => {
    const activeId = get().activeWorkspaceId;
    if (!activeId) return null;
    return get().workspaces[activeId] ?? null;
  },
}));
