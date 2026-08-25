import { useMemo } from "react";
import { useCharlieStore, type PresentationIntent } from "../store/charlie";
import { useWorkspaceStore, type WorkspaceInstance } from "../layout/workspaceStore";
import { useWidgetStore, type WidgetInstance } from "../layout/widgetStore";

export type SceneMode = "idle" | "active" | "dense";
export type CorePosition = "center" | "dock_bottom_right";

export interface SceneProjection {
  sceneMode: SceneMode;
  corePosition: CorePosition;
  activeWorkspace: WorkspaceInstance | null;
  activeAttention: PresentationIntent | null;
  activeWidgets: WidgetInstance[];
  activeNotifications: PresentationIntent[];
  activeCaption: string | null;
  coreState: string;
}

export function useSceneProjection(): SceneProjection {
  const presentationIntents = useCharlieStore((s) => s.presentationIntents);
  const activeCaption = useCharlieStore((s) => s.activeCaption);
  const coreState = useCharlieStore((s) => s.coreState);

  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const activeWorkspaceId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const widgets = useWidgetStore((s) => s.widgets);

  return useMemo(() => {
    const intents = Object.values(presentationIntents);

    // 1. Primary workspace from WorkspaceManager (fallback to raw intent if not yet synced)
    let activeWorkspace: WorkspaceInstance | null = null;
    if (activeWorkspaceId && workspaces[activeWorkspaceId]) {
      const candidate = workspaces[activeWorkspaceId];
      if (candidate.lifecycleState === "active") {
        activeWorkspace = candidate;
      }
    }

    if (!activeWorkspace) {
      const rawWs = intents.find((i) => i.kind === "workspace");
      // A known minimized instance is authoritative local lifecycle state for
      // rendering. Do not resurrect it from the raw intent until runtime
      // sends a fresh focus update.
      if (rawWs && !workspaces[rawWs.id]) {
        activeWorkspace = {
          id: rawWs.id,
          type: rawWs.workspaceType || "custom",
          presentationIntentId: rawWs.id,
          taskId: rawWs.taskId ?? null,
          title: rawWs.title || `WORKSPACE // ${(rawWs.workspaceType || "CANVAS").toUpperCase()}`,
          summary: rawWs.summary || "",
          status: "active",
          lifecycleState: "active",
          focused: true,
          openedAt: new Date().toISOString(),
          lastFocusedAt: new Date().toISOString(),
          persistent: rawWs.dismissPolicy === "persistent",
          replayable: rawWs.replayable,
          contentState: rawWs.content || {},
        };
      }
    }

    // 2. Attention modal intent (if any)
    const activeAttention = intents.find((i) => i.kind === "attention") ?? null;

    // 3. Active widgets from WidgetManager
    const activeWidgetList = Object.values(widgets).filter((w) => !w.minimized);

    // 4. Active notifications
    const activeNotifications = intents.filter((i) => i.kind === "notification");

    // 5. Core Position: Docked when workspace is active, centered on idle/caption/widget
    const corePosition: CorePosition = activeWorkspace ? "dock_bottom_right" : "center";

    // 6. Scene mode
    let sceneMode: SceneMode = "idle";
    if (activeWorkspace || activeAttention) {
      sceneMode = "active";
    } else if (activeWidgetList.length > 1) {
      sceneMode = "dense";
    } else if (activeWidgetList.length === 1 || activeNotifications.length > 0 || activeCaption) {
      sceneMode = "active";
    }

    return {
      sceneMode,
      corePosition,
      activeWorkspace,
      activeAttention,
      activeWidgets: activeWidgetList,
      activeNotifications,
      activeCaption,
      coreState,
    };
  }, [
    presentationIntents,
    activeCaption,
    coreState,
    workspaces,
    activeWorkspaceId,
    widgets,
  ]);
}
