import type { ReactElement } from "react";
import type { PresentationIntent } from "../store/charlie";
import { ContentMaskLayer } from "./ContentMaskLayer";

interface WorkspaceLayerProps {
  activeWorkspace: PresentationIntent | null;
  onDismiss?: (id: string) => void;
}

export function WorkspaceLayer({ activeWorkspace, onDismiss }: WorkspaceLayerProps): ReactElement | null {
  if (!activeWorkspace) return null;

  return (
    <div className="charlie-workspace-layer" role="region" aria-label="Primary Workspace">
      <div className="charlie-workspace-host">
        <ContentMaskLayer>
          <div className="p-6 h-full flex flex-col justify-between">
            {/* Header */}
            <div className="flex items-center justify-between border-b border-cyan-500/20 pb-4">
              <div className="flex items-center gap-3">
                <span className="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-pulse" />
                <h2 className="text-base font-semibold tracking-wide text-cyan-200">
                  {activeWorkspace.title || `WORKSPACE // ${activeWorkspace.workspaceType?.toUpperCase() || "CANVAS"}`}
                </h2>
              </div>
              {onDismiss && (
                <button
                  type="button"
                  onClick={() => onDismiss(activeWorkspace.id)}
                  className="px-3 py-1 text-xs rounded bg-cyan-950/60 border border-cyan-500/30 text-cyan-300 hover:bg-cyan-900/60 transition cursor-pointer"
                >
                  Close [Esc]
                </button>
              )}
            </div>

            {/* Body */}
            <div className="my-auto py-6 text-sm text-cyan-100/90 max-w-2xl leading-relaxed">
              <p className="font-mono text-xs text-cyan-400 mb-2">// {activeWorkspace.workspaceType || "primary"}</p>
              <p className="text-base text-slate-100">{activeWorkspace.summary}</p>
            </div>

            {/* Footer telemetry */}
            <div className="text-[11px] font-mono text-cyan-400/50 flex justify-between border-t border-cyan-500/10 pt-3">
              <span>PRIORITY: {activeWorkspace.priority}</span>
              <span>DISMISS: {activeWorkspace.dismissPolicy}</span>
            </div>
          </div>
        </ContentMaskLayer>
      </div>
    </div>
  );
}
