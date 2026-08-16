import { lazy, Suspense, type ReactElement } from "react";
import { ContentMaskLayer } from "./ContentMaskLayer";
import { useWorkspaceStore, type WorkspaceInstance } from "../layout/workspaceStore";
import { SurfaceComposer } from "../composer/SurfaceComposer";
import { ResearchWorkspace } from "./workspaces/ResearchWorkspace";
import { BriefingWorkspace } from "./workspaces/BriefingWorkspace";
import { SystemWorkspace } from "./workspaces/SystemWorkspace";
import { TasksWorkspace } from "./workspaces/TasksWorkspace";
import { VisionWorkspace } from "./workspaces/VisionWorkspace";
import { DocumentWorkspace } from "./workspaces/DocumentWorkspace";
import { TerminalWorkspace } from "./workspaces/TerminalWorkspace";
import { ConversationWorkspace } from "./workspaces/ConversationWorkspace";

// Lazy-load MapWorkspace to avoid eagerly loading MapLibre/Deck.gl on idle Charlie
const MapWorkspace = lazy(() =>
  import("./workspaces/MapWorkspace").then((m) => ({ default: m.MapWorkspace }))
);

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

  const wsType = (active.type || "custom").toLowerCase();
  const isSpatialMap = wsType === "map" || wsType === "spatial";
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
              nodes: [],
            }
          }
        />
      );
    }

    switch (wsType) {
      case "research":
        return <ResearchWorkspace workspace={active} />;
      case "briefing":
        return <BriefingWorkspace workspace={active} />;
      case "system":
      case "telemetry":
        return <SystemWorkspace workspace={active} />;
      case "tasks":
      case "task":
      case "plans":
        return <TasksWorkspace workspace={active} />;
      case "map":
      case "spatial":
        return (
          <Suspense
            fallback={
              <div className="w-full h-full flex items-center justify-center bg-[#020710] font-mono text-cyan-400 text-xs">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-cyan-400 animate-ping" />
                  <span>INITIALIZING SPATIAL ENGINE...</span>
                </div>
              </div>
            }
          >
            <MapWorkspace workspace={active} />
          </Suspense>
        );
      case "vision":
      case "camera":
        return <VisionWorkspace workspace={active} />;
      case "document":
      case "report":
      case "file":
        return <DocumentWorkspace workspace={active} />;
      case "terminal":
        return <TerminalWorkspace workspace={active} />;
      case "conversation":
      case "chat":
        return <ConversationWorkspace workspace={active} />;
      default:
        return (
          <div className="my-auto py-6 text-sm text-cyan-100/90 max-w-2xl leading-relaxed">
            <p className="font-mono text-xs text-cyan-400 mb-2">// {wsType || "primary"}</p>
            <p className="text-base text-slate-100">{active.summary || "No active stream details."}</p>
          </div>
        );
    }
  };

  // Edge-to-Edge Spatial Mode for Map & Spatial Workspaces
  // ("The map IS the workspace" — no outer rounded card, no generic header box)
  if (isSpatialMap) {
    return (
      <div className="charlie-workspace-layer !p-0 !inset-0" role="region" aria-label="Spatial Map Workspace">
        <div className="w-full h-full relative pointer-events-auto overflow-hidden">
          {/* Floating Minimal HUD Controls in Top-Right Safe Zone */}
          <div className="absolute top-4 right-4 z-40 flex items-center gap-1.5 pointer-events-auto font-mono">
            <button
              type="button"
              onClick={handleMinimize}
              className="px-2 py-0.5 text-xs rounded bg-slate-950/80 border border-cyan-500/25 text-slate-400 hover:text-cyan-300 hover:border-cyan-400/60 transition cursor-pointer backdrop-blur-md shadow-lg"
              title="Minimize [_]"
              aria-label="Minimize workspace"
            >
              _
            </button>
            <button
              type="button"
              onClick={handleClose}
              className="px-2 py-0.5 text-xs rounded bg-slate-950/80 border border-cyan-500/25 text-slate-400 hover:text-rose-400 hover:border-rose-500/50 transition cursor-pointer backdrop-blur-md shadow-lg"
              title="Close [ESC]"
              aria-label="Close workspace"
            >
              ×
            </button>
          </div>

          <ContentMaskLayer>
            <div className="w-full h-full relative">
              <Suspense
                fallback={
                  <div className="w-full h-full flex items-center justify-center bg-[#020710] font-mono text-cyan-400 text-xs">
                    <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-cyan-400 animate-ping" />
                      <span>INITIALIZING SPATIAL ENGINE...</span>
                    </div>
                  </div>
                }
              >
                <MapWorkspace workspace={active} />
              </Suspense>
            </div>
          </ContentMaskLayer>
        </div>
      </div>
    );
  }

  // Standard Framed Workspace Card Mode
  return (
    <div className="charlie-workspace-layer" role="region" aria-label={`Primary Workspace ${wsTitle}`}>
      <ContentMaskLayer>
        <div className="charlie-panel p-6 flex flex-col justify-between h-full pointer-events-auto relative">
          <div className="flex items-center justify-between pb-2.5 border-b border-cyan-500/10 text-[11px] font-mono text-cyan-400/80 select-none">
            <div className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
              <span className="tracking-wider uppercase font-medium">{wsTitle}</span>
            </div>
            <div className="flex items-center gap-1 opacity-60 hover:opacity-100 transition-opacity">
              <button
                type="button"
                onClick={handleMinimize}
                className="px-2 py-0.5 text-[11px] rounded bg-slate-900/40 border border-cyan-500/15 text-slate-400 hover:text-cyan-300 hover:border-cyan-400/30 transition cursor-pointer"
                title="Minimize [_]"
                aria-label="Minimize workspace"
              >
                _
              </button>
              <button
                type="button"
                onClick={handleClose}
                className="px-2 py-0.5 text-[11px] rounded bg-slate-900/40 border border-cyan-500/15 text-slate-400 hover:text-rose-400 hover:border-rose-500/30 transition cursor-pointer"
                title="Close [ESC]"
                aria-label="Close workspace"
              >
                ×
              </button>
            </div>
          </div>

          <div className="flex-1 w-full min-h-0 pt-4 overflow-y-auto custom-scrollbar flex flex-col">
            {renderWorkspaceContent()}
          </div>
        </div>
      </ContentMaskLayer>
    </div>
  );
}
