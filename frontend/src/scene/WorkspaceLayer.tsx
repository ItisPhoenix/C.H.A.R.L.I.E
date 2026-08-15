import type { ReactElement } from "react";
import { ContentMaskLayer } from "./ContentMaskLayer";
import { useWorkspaceStore, type WorkspaceInstance } from "../layout/workspaceStore";
import { SurfaceComposer } from "../composer/SurfaceComposer";
import { ResearchWorkspace } from "./workspaces/ResearchWorkspace";
import { BriefingWorkspace } from "./workspaces/BriefingWorkspace";
import { SystemWorkspace } from "./workspaces/SystemWorkspace";
import { TasksWorkspace } from "./workspaces/TasksWorkspace";
import { MapWorkspace } from "./workspaces/MapWorkspace";
import { VisionWorkspace } from "./workspaces/VisionWorkspace";
import { DocumentWorkspace } from "./workspaces/DocumentWorkspace";
import { TerminalWorkspace } from "./workspaces/TerminalWorkspace";
import { ConversationWorkspace } from "./workspaces/ConversationWorkspace";

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
      case "research":
        return <ResearchWorkspace workspace={active} />;
      case "briefing":
      case "news":
        return <BriefingWorkspace workspace={active} />;
      case "system":
      case "diagnostics":
      case "system_monitor":
        return <SystemWorkspace workspace={active} />;
      case "tasks":
        return <TasksWorkspace workspace={active} />;
      case "map":
      case "spatial":
        return <MapWorkspace workspace={active} />;
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

  return (
    <div className="charlie-workspace-layer" role="region" aria-label="Primary Workspace">
      <div className="charlie-workspace-host">
        <ContentMaskLayer>
          <div className="p-4 sm:p-6 h-full flex flex-col justify-between">
            {/* Subtle Floating HUD Header */}
            <div className="flex items-center justify-between pb-3 mb-2 border-b border-cyan-500/15">
              <div className="flex items-center gap-3">
                <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
                <h2 className="text-sm sm:text-base font-bold tracking-wider text-slate-100 uppercase font-sans">
                  {wsTitle}
                </h2>
              </div>

              {/* Minimal HUD Controls (Less desktop-window like) */}
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleMinimize}
                  className="px-2 py-0.5 text-xs rounded bg-slate-950/60 border border-cyan-500/20 text-slate-400 hover:text-cyan-300 hover:border-cyan-400/50 transition cursor-pointer font-mono"
                  title="Minimize [_]"
                  aria-label="Minimize workspace"
                >
                  _
                </button>
                <button
                  type="button"
                  onClick={handleClose}
                  className="px-2 py-0.5 text-xs rounded bg-slate-950/60 border border-cyan-500/20 text-slate-400 hover:text-cyan-300 hover:border-cyan-400/50 transition cursor-pointer font-mono"
                  title="Close workspace [Esc]"
                  aria-label="Close workspace"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-auto">
              {renderWorkspaceContent()}
            </div>
          </div>
        </ContentMaskLayer>
      </div>
    </div>
  );
}
