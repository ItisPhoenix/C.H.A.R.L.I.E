import { useMemo } from "react";
import { useCharlieStore, type PresentationIntent } from "../store/charlie";

export type SceneMode = "idle" | "active" | "dense";
export type CorePosition = "center" | "dock_bottom_right";

export interface SceneProjection {
  sceneMode: SceneMode;
  corePosition: CorePosition;
  activeWorkspace: PresentationIntent | null;
  activeAttention: PresentationIntent | null;
  activeWidgets: PresentationIntent[];
  activeNotifications: PresentationIntent[];
  activeCaption: string | null;
  coreState: string;
  audioLevel: number;
}

export function useSceneProjection(): SceneProjection {
  const presentationIntents = useCharlieStore((s) => s.presentationIntents);
  const activeCaption = useCharlieStore((s) => s.activeCaption);
  const coreState = useCharlieStore((s) => s.coreState);
  const audioLevel = useCharlieStore((s) => s.audioLevel);

  return useMemo(() => {
    const intents = Object.values(presentationIntents);

    // 1. Primary workspace intent (if any)
    const activeWorkspace = intents.find((i) => i.kind === "workspace") ?? null;

    // 2. Attention modal intent (if any)
    const activeAttention = intents.find((i) => i.kind === "attention") ?? null;

    // 3. Active widgets
    const activeWidgets = intents.filter((i) => i.kind === "widget" || i.kind === "composed_surface");

    // 4. Active notifications
    const activeNotifications = intents.filter((i) => i.kind === "notification");

    // 5. Core Position: Docked when workspace is open, centered on idle/caption/widget
    const corePosition: CorePosition = activeWorkspace ? "dock_bottom_right" : "center";

    // 6. Scene mode
    let sceneMode: SceneMode = "idle";
    if (activeWorkspace || activeAttention) {
      sceneMode = "active";
    } else if (activeWidgets.length > 1) {
      sceneMode = "dense";
    } else if (activeWidgets.length === 1 || activeNotifications.length > 0 || activeCaption) {
      sceneMode = "active";
    }

    return {
      sceneMode,
      corePosition,
      activeWorkspace,
      activeAttention,
      activeWidgets,
      activeNotifications,
      activeCaption,
      coreState,
      audioLevel,
    };
  }, [presentationIntents, activeCaption, coreState, audioLevel]);
}
