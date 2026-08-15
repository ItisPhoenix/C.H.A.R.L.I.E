import type { ReactElement } from "react";
import { ContentMaskLayer } from "./ContentMaskLayer";
import { useWorkspaceStore, type WorkspaceInstance } from "../layout/workspaceStore";
import { Tasks } from "../dashboard/Tasks";
import { SystemMonitor } from "../dashboard/SystemMonitor";
import { Terminal } from "../dashboard/Terminal";
import { Chat } from "../dashboard/Chat";

import { SurfaceComposer } from "../composer/SurfaceComposer";

interface WorkspaceLayerProps {
  activeWorkspace?: WorkspaceInstance | null;
  onDismiss?: (id: string) => void;
}

export function WorkspaceLayer({ activeWorkspace: propWorkspace, onDismiss }: WorkspaceLayerProps): ReactElement | null {
  const storeActiveWorkspace = useWorkspaceStore((s) => s.getActiveWorkspace());
  const minimizeWorkspace = useWorkspaceStore((s) => s.minimizeWorkspace);
  const closeWorkspace = useWorkspaceStore((s) => s.closeWorkspace);

  const active = propWorkspace !== undefined ? propWorkspace : storeActiveWorkspace;
  if (!active || active.lifecycleState === "minimized" || active.lifecycleState === "closed") {
    return null;
  }

  const handleClose = () => {
    if (onDismiss) {
      onDismiss(active.id);
    } else {
      closeWorkspace(active.id);
    }
  };

  const handleMinimize = () => {
    minimizeWorkspace(active.id);
  };

  // Render workspace body based on semantic type
  const wsType = (active.type || "custom").toLowerCase();
  const wsTitle = active.title || `WORKSPACE // ${wsType.toUpperCase()}`;

  const renderWorkspaceContent = () => {
    // Check if composed surface payload is present
    const surfacePayload = (active.contentState?.surface_spec ||
      (active.contentState?.schema_version ? active.contentState : null)) as Record<string, unknown> | null;

    if (surfacePayload || wsType === "composed_surface") {
      return (
        <SurfaceComposer
          spec={
            surfacePayload || {
              schema_version: 1,
              surface_id: active.id,
              title: wsTitle,
              target: "workspace",
              revision: 1,
              primitives: [{ type: "text", data: { text: active.summary || "Composed surface" } }],
            }
          }
        />
      );
    }

    switch (wsType) {
      case "tasks":
        return <Tasks />;
      case "system":
        return <SystemMonitor />;
      case "terminal":
        return <Terminal />;
      case "conversation":
      case "chat":
        return <Chat />;
      case "research":
      case "briefing":
      case "camera":
      case "map":
      case "settings":
      case "diagnostics":
      default:
        return (
          <div className="my-auto py-6 text-sm text-cyan-100/90 max-w-2xl leading-relaxed">
            <p className="font-mono text-xs text-cyan-400 mb-2">// {wsType || "primary"}</p>
            <p className="text-base text-slate-100">{active.summary || "No active stream details."}</p>
          </div>
        );
    }
  };

  return (
    <div className="charlie-workspace-layer" role="region" aria-label="Primary Workspace">
      <div className="charlie-workspace-host">
        <ContentMaskLayer>
          <div className="p-6 h-full flex flex-col justify-between">
            {/* Header */}
            <div className="flex items-center justify-between border-b border-cyan-500/20 pb-4">
              <div className="flex items-center gap-3">
                <span className="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-pulse" />
                <h2 className="text-base font-semibold tracking-wide text-cyan-200 uppercase">
                  {wsTitle}
                </h2>
              </div>

              {/* Window-agnostic Minimal Controls */}
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleMinimize}
                  className="px-2.5 py-1 text-xs rounded bg-cyan-950/60 border border-cyan-500/30 text-cyan-300 hover:bg-cyan-900/60 transition cursor-pointer"
                  title="Minimize to Recent [_]"
                  aria-label="Minimize workspace"
                >
                  _
                </button>
                <button
                  type="button"
                  onClick={handleClose}
                  className="px-2.5 py-1 text-xs rounded bg-cyan-950/60 border border-cyan-500/30 text-cyan-300 hover:bg-cyan-900/60 transition cursor-pointer"
                  title="Close workspace [Esc]"
                  aria-label="Close workspace"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-auto my-3">
              {renderWorkspaceContent()}
            </div>

            {/* Footer telemetry */}
            <div className="text-[11px] font-mono text-cyan-400/50 flex justify-between border-t border-cyan-500/10 pt-3">
              <span>TYPE: {wsType.toUpperCase()}</span>
              <span>STATUS: {(active.status || "ACTIVE").toUpperCase()}</span>
            </div>
          </div>
        </ContentMaskLayer>
      </div>
    </div>
  );
}
