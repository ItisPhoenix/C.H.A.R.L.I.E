import type { ReactElement } from "react";

export function MapAttribution(): ReactElement {
  return (
    <div className="absolute bottom-4 left-6 z-20 pointer-events-auto font-mono text-[9px] text-slate-500/80 hover:text-slate-400 transition select-none flex items-center gap-2">
      <span>© OpenStreetMap contributors</span>
      <span>•</span>
      <span>© CARTO</span>
      <span>•</span>
      <span>USGS / NASA EONET</span>
    </div>
  );
}
