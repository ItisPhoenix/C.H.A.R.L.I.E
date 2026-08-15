import { useEffect, useState, type ReactElement } from "react";
import { useNavigate } from "react-router-dom";
import { useCharlieStore } from "../store/charlie";
import { useSceneProjection } from "./sceneState";
import { EnvironmentLayer } from "./EnvironmentLayer";
import { WorkspaceLayer } from "./WorkspaceLayer";
import { WidgetLayer } from "./WidgetLayer";
import { ContextLayer } from "./ContextLayer";
import { CharlieCore } from "./CharlieCore";
import "./scene.css";

export function CharlieScene(): ReactElement {
  const navigate = useNavigate();
  const projection = useSceneProjection();
  const dismissIntent = useCharlieStore((s) => s.dismissPresentationIntent);

  const [debugMode, setDebugMode] = useState(false);

  // Keyboard shortcut listener: Escape to dismiss active presentation; Ctrl+Shift+D for debug mode
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (projection.activeAttention) {
          dismissIntent(projection.activeAttention.id);
        } else if (projection.activeWorkspace) {
          dismissIntent(projection.activeWorkspace.id);
        } else if (projection.activeWidgets.length > 0) {
          const first = projection.activeWidgets[0];
          if (first) dismissIntent(first.id);
        }
      } else if (e.ctrlKey && e.shiftKey && (e.key === "D" || e.key === "d")) {
        setDebugMode((prev) => !prev);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [projection, dismissIntent]);

  const handleClearScreen = () => {
    // Clear temporary/non-pinned widgets and workspaces
    for (const w of projection.activeWidgets) {
      dismissIntent(w.id);
    }
    if (projection.activeWorkspace) {
      dismissIntent(projection.activeWorkspace.id);
    }
  };

  return (
    <main
      className="charlie-scene-root"
      data-scene-mode={projection.sceneMode}
      data-core-position={projection.corePosition}
      data-core-state={projection.coreState}
    >
      {/* 1. Environment Layer (Opaque dark base, dual technical grid, radial light, vignette, grain, framing) */}
      <EnvironmentLayer
        corePosition={projection.corePosition}
        hasWorkspace={Boolean(projection.activeWorkspace)}
      />

      {/* 2. Workspace Layer (Primary spatial canvas for research/briefing/terminal/camera) */}
      <WorkspaceLayer
        activeWorkspace={projection.activeWorkspace}
        onDismiss={dismissIntent}
      />

      {/* 3. Widget Layer (Contextual widgets placed in designated layout zones) */}
      <WidgetLayer
        widgets={projection.activeWidgets}
        onDismiss={dismissIntent}
      />

      {/* 4. Context Layer (Near-core captions, transient notifications, attention modals) */}
      <ContextLayer
        captionText={projection.activeCaption}
        notifications={projection.activeNotifications}
        activeAttention={projection.activeAttention}
        onDismissIntent={dismissIntent}
      />

      {/* 5. Charlie Core (Center <-> Dock_Bottom_Right spatial transition, animation hooks) */}
      <CharlieCore
        position={projection.corePosition}
        coreState={projection.coreState}
        onClearScreen={handleClearScreen}
        onOpenLegacyDashboard={() => navigate("/dashboard")}
      />

      {/* 6. Developer Debug Mode Overlays */}
      {debugMode && (
        <div className="absolute top-2 left-2 p-3 bg-black/90 border border-cyan-500/50 rounded text-[11px] font-mono text-cyan-300 z-50 pointer-events-auto">
          <div className="font-bold mb-1">// DEBUG: SCENE PROJECTION</div>
          <div>Mode: {projection.sceneMode}</div>
          <div>Core Pos: {projection.corePosition}</div>
          <div>Core State: {projection.coreState}</div>
          <div>Audio Level: {projection.audioLevel.toFixed(2)}</div>
          <div>Active WS: {projection.activeWorkspace?.workspaceType || "none"}</div>
          <div>Widgets ({projection.activeWidgets.length}): {projection.activeWidgets.map((w) => w.id).join(", ") || "none"}</div>
          <div className="mt-2 text-slate-400">Press Ctrl+Shift+D to hide</div>
        </div>
      )}
    </main>
  );
}
