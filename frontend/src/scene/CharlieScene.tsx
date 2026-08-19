import { useEffect, useState, type ReactElement } from "react";
import { useCharlieStore } from "../store/charlie";
import { useWorkspaceStore } from "../layout/workspaceStore";
import { useWidgetStore } from "../layout/widgetStore";
import { useSceneProjection } from "./sceneState";
import { EnvironmentLayer } from "./EnvironmentLayer";
import { WorkspaceLayer } from "./WorkspaceLayer";
import { WidgetLayer } from "./WidgetLayer";
import { ContextLayer } from "./ContextLayer";
import { CharlieCore } from "./CharlieCore";
import { RecentWorkspacesModal } from "../layout/RecentWorkspacesModal";
import { SettingsModal } from "./SettingsModal";
import { TaskSwitcher } from "./TaskSwitcher";
import type { ZoneContext } from "../layout/zones";
import "./scene.css";

export function CharlieScene(): ReactElement | null {
  const projection = useSceneProjection();

  // Stores
  const presentationIntents = useCharlieStore((s) => s.presentationIntents);
  const dismissIntent = useCharlieStore((s) => s.dismissPresentationIntent);
  const hudVisible = useCharlieStore((s) => s.hudVisible);
  const settingsIntentId = Object.values(presentationIntents).find(
    (intent) => intent.kind === "workspace" && intent.workspaceType === "settings",
  )?.id;

  const openWorkspace = useWorkspaceStore((s) => s.openWorkspace);
  const minimizeWorkspace = useWorkspaceStore((s) => s.minimizeWorkspace);
  const clearWorkspaces = useWorkspaceStore((s) => s.clearWorkspaces);

  const upsertWidget = useWidgetStore((s) => s.upsertWidget);
  const clearScreenWidgets = useWidgetStore((s) => s.clearScreen);
  const focusedEscapeWidgets = useWidgetStore((s) => s.focusedEscape);

  const [debugMode, setDebugMode] = useState(false);
  const [recentModalOpen, setRecentModalOpen] = useState(false);
  const [settingsModalOpen, setSettingsModalOpen] = useState(false);

  useEffect(() => {
    (window as unknown as { __OPEN_SETTINGS__?: () => void; __CLOSE_SETTINGS__?: () => void }).__OPEN_SETTINGS__ = () => setSettingsModalOpen(true);
    (window as unknown as { __OPEN_SETTINGS__?: () => void; __CLOSE_SETTINGS__?: () => void }).__CLOSE_SETTINGS__ = () => setSettingsModalOpen(false);
  }, []);

  // Sync incoming PresentationIntents to WorkspaceManager and WidgetManager
  useEffect(() => {
    const hasWorkspace = Object.values(presentationIntents).some(
      (i) => i.kind === "workspace" && i.workspaceType !== "settings",
    );
    const zoneCtx: ZoneContext = {
      viewport: { width: window.innerWidth, height: window.innerHeight },
      safeMargin: { x: Math.min(Math.max(16, window.innerWidth * 0.035), 48), y: Math.min(Math.max(16, window.innerHeight * 0.035), 48) },
      coreBounds: {
        x: window.innerWidth * 0.5 - 150,
        y: window.innerHeight * 0.5 - 150,
        width: 300,
        height: 300,
      },
      workspaceBounds: hasWorkspace
        ? {
            x: window.innerWidth * 0.1,
            y: window.innerHeight * 0.1,
            width: window.innerWidth * 0.8,
            height: window.innerHeight * 0.8,
          }
        : null,
    };

    for (const intent of Object.values(presentationIntents)) {
      if (intent.kind === "workspace") {
        if (intent.workspaceType !== "settings") openWorkspace(intent);
      } else if (intent.kind === "widget" || intent.kind === "composed_surface") {
        upsertWidget(intent, zoneCtx);
      }
    }
  }, [presentationIntents, openWorkspace, upsertWidget]);

  useEffect(() => {
    setSettingsModalOpen(Boolean(settingsIntentId));
  }, [settingsIntentId]);

  // Keyboard shortcut listener: Focused Escape & Ctrl+Shift+D debug overlay
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // Strict non-cascading Escape: only acts on currently focused surface
        if (projection.activeAttention) {
          dismissIntent(projection.activeAttention.id);
        } else if (focusedEscapeWidgets()) {
          // Focused widget handled
        } else if (projection.activeWorkspace) {
          minimizeWorkspace(projection.activeWorkspace.id);
          dismissIntent(projection.activeWorkspace.id);
        }
      } else if (e.ctrlKey && e.shiftKey && (e.key === "D" || e.key === "d")) {
        setDebugMode((prev) => !prev);
      } else if ((e.ctrlKey || e.metaKey) && e.key === ",") {
        setSettingsModalOpen((prev) => !prev);
      }
    };

    (window as unknown as { __OPEN_SETTINGS__?: () => void }).__OPEN_SETTINGS__ = () => setSettingsModalOpen(true);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      delete (window as unknown as { __OPEN_SETTINGS__?: () => void }).__OPEN_SETTINGS__;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [projection.activeAttention, projection.activeWorkspace, dismissIntent, minimizeWorkspace, focusedEscapeWidgets]);

  const handleClearScreen = () => {
    // 1. Dismiss all temporary (unpinned) widgets; keep pinned widgets
    clearScreenWidgets();

    // 2. Minimize active workspace into Recent
    clearWorkspaces();

    // 3. Clear transient presentation intents
    for (const intent of Object.values(presentationIntents)) {
      if (intent.kind !== "attention") {
        dismissIntent(intent.id);
      }
    }
  };

  if (!hudVisible) return null;

  return (
    <main
      className="charlie-scene-root"
      data-scene-mode={projection.sceneMode}
      data-core-position={projection.corePosition}
      data-core-state={projection.coreState}
    >
      {/* 1. Environment Layer (Opaque dark base, technical grid, radial light, vignette, grain, framing) */}
      <EnvironmentLayer
        corePosition={projection.corePosition}
        hasWorkspace={Boolean(projection.activeWorkspace)}
      />

      {/* 2. Workspace Layer (Primary spatial canvas for research/briefing/terminal/camera) */}
      <WorkspaceLayer
        activeWorkspace={projection.activeWorkspace}
        onDismiss={(id) => {
          minimizeWorkspace(id);
          dismissIntent(id);
        }}
      />

      {/* 3. Widget Layer (Contextual draggable/resizable/pinnable widgets) */}
      <WidgetLayer />

      {/* 4. Context Layer (Near-core captions, transient notifications, attention modals) */}
      <ContextLayer
        captionText={projection.activeCaption}
        notifications={projection.activeNotifications}
        activeAttention={projection.activeAttention}
        onDismissIntent={dismissIntent}
      />

      {/* 5. Contextual Task Switcher (Only visible when >1 tasks or active) */}
      <TaskSwitcher />

      {/* 6. Charlie Core (Center <-> Dock_Bottom_Right spatial transition, animation hooks) */}
      <CharlieCore
        position={projection.corePosition}
        coreState={projection.coreState}
        activeWorkspaceType={projection.activeWorkspace?.type}
        onClearScreen={handleClearScreen}
        onOpenRecent={() => setRecentModalOpen(true)}
        onOpenSettings={() => setSettingsModalOpen(true)}
      />

      {/* 7. Recent Workspaces Modal */}
      <RecentWorkspacesModal
        isOpen={recentModalOpen}
        onClose={() => setRecentModalOpen(false)}
      />

      {/* 8. Settings Modal Overlay */}
      <SettingsModal
        isOpen={settingsModalOpen}
        onClose={() => {
          setSettingsModalOpen(false);
          if (settingsIntentId) dismissIntent(settingsIntentId);
        }}
      />

      {/* 7. Developer Debug Mode Overlays */}
      {debugMode && (
        <div className="absolute top-2 left-2 p-3 bg-black/90 border border-cyan-500/50 rounded text-[11px] font-mono text-cyan-300 z-50 pointer-events-auto">
          <div className="font-bold mb-1">// DEBUG: SCENE PROJECTION</div>
          <div>Mode: {projection.sceneMode}</div>
          <div>Core Pos: {projection.corePosition}</div>
          <div>Core State: {projection.coreState}</div>
          <div>Audio Level: {useCharlieStore.getState().audioLevel.toFixed(2)}</div>
          <div>Active WS: {projection.activeWorkspace?.type || "none"}</div>
          <div>Widgets ({projection.activeWidgets.length}): {projection.activeWidgets.map((w) => `${w.widgetType}(${w.pinned ? "P" : "T"})`).join(", ") || "none"}</div>
          <div className="mt-2 text-slate-400">Press Ctrl+Shift+D to hide</div>
        </div>
      )}
    </main>
  );
}
