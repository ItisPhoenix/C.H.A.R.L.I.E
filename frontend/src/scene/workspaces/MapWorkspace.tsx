import type { ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { SpatialMapPrimitive, type SpatialMapData } from "../../composer/primitives/SpatialMapPrimitive";

export function MapWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const title = String(content.title || workspace.title || "SPATIAL MAP WORKSPACE").replace(/^WORKSPACE\s*\/\/\s*/i, "");
  const subtitle = String(content.subtitle || "GEOSPATIAL & TACTICAL NAVIGATION");

  const mode = (typeof content.mode === "string" ? content.mode : "geo") as SpatialMapData["mode"];
  const mapTitle = String(content.map_title || "INTERACTIVE SPATIAL ENVIRONMENT");
  const mapSubtitle = String(content.map_subtitle || "LAYER ACCELERATED VECTOR ENGINE");

  return (
    <div className="w-full h-full flex flex-col justify-between font-mono select-none text-left p-2 overflow-y-auto space-y-4">
      <div className="flex items-start justify-between border-b border-cyan-500/20 pb-3">
        <div>
          <div className="text-[10px] text-cyan-400 font-bold tracking-widest uppercase mb-0.5 flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
            MAP / SPATIAL NAVIGATION
          </div>
          <h1 className="text-xl font-bold text-slate-100 uppercase tracking-tight font-sans">
            {title}
          </h1>
          <div className="text-xs text-cyan-400/70 tracking-widest uppercase">
            {subtitle}
          </div>
        </div>
      </div>

      <div className="flex-1 w-full min-h-[440px] flex flex-col">
        <SpatialMapPrimitive
          data={{
            mode,
            title: mapTitle,
            subtitle: mapSubtitle,
            ...((content.spatial_map as SpatialMapData) || {}),
          }}
        />
      </div>
    </div>
  );
}
