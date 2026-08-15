import type { ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

export function MapPlaceholderPrimitive({ primitive }: { primitive: PrimitiveSpec }): ReactElement {
  const data = primitive.data || {};
  const location = String(data.location ?? "Target Coordinates");
  const lat = data.lat !== undefined ? Number(data.lat) : null;
  const lon = data.lon !== undefined ? Number(data.lon) : null;

  return (
    <div className="w-full my-2 p-4 rounded-xl border border-cyan-500/20 bg-slate-950/60 text-center flex flex-col items-center justify-center gap-1.5 min-h-[140px] relative overflow-hidden">
      <div className="text-cyan-400 font-mono text-xs">◈ MAP SPATIAL HOST // {location.toUpperCase()}</div>
      {lat !== null && lon !== null && (
        <div className="text-[10px] font-mono text-slate-400">
          LAT: {lat.toFixed(4)} | LON: {lon.toFixed(4)}
        </div>
      )}
      <div className="text-[11px] text-slate-500 italic mt-1">
        [Spatial map projection ready for Map Workspace host]
      </div>
    </div>
  );
}
