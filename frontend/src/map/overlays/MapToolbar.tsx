import type { ReactElement } from "react";
import { useMapStore } from "../mapStore";

export function MapToolbar(): ReactElement {
  const pitch = useMapStore((s) => s.pitch);
  const dispatchCommand = useMapStore((s) => s.dispatchCommand);
  const clearMap = useMapStore((s) => s.clearMap);

  const is3D = pitch > 15;

  const toggle3D = () => {
    dispatchCommand({
      type: "set_pitch",
      pitch: is3D ? 0 : 55,
    });
  };

  const handleZoomIn = () => {
    dispatchCommand({ type: "zoom_in" });
  };

  const handleZoomOut = () => {
    dispatchCommand({ type: "zoom_out" });
  };

  const handleResetNorth = () => {
    dispatchCommand({ type: "reset_north" });
  };

  return (
    <div className="absolute bottom-6 left-6 z-20 flex items-center gap-1 p-1 rounded-lg bg-slate-950/85 border border-cyan-500/25 backdrop-blur-md font-mono text-xs pointer-events-auto shadow-xl">
      <button
        type="button"
        onClick={toggle3D}
        className={`px-2.5 py-1 rounded transition cursor-pointer font-bold text-[11px] ${
          is3D
            ? "bg-cyan-950 text-cyan-200 border border-cyan-400/50 shadow-sm"
            : "text-slate-400 hover:text-slate-200 hover:bg-slate-900/60"
        }`}
        title="Toggle 2D / 3D Tilt Mode"
      >
        {is3D ? "3D" : "2D"}
      </button>

      <button
        type="button"
        onClick={handleResetNorth}
        className="px-2 py-1 rounded text-slate-400 hover:text-cyan-300 hover:bg-slate-900/60 transition cursor-pointer flex items-center gap-1 text-[11px]"
        title="Reset North Up (N)"
      >
        <svg className="w-3 h-3 text-cyan-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polygon points="12 2 19 21 12 17 5 21 12 2" fill="currentColor" fillOpacity="0.3" />
        </svg>
        <span>N</span>
      </button>

      <div className="w-[1px] h-4 bg-cyan-500/20 mx-0.5" />

      <button
        type="button"
        onClick={handleZoomIn}
        className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-300 hover:text-cyan-200 hover:bg-slate-800/60 transition cursor-pointer font-bold text-sm"
        title="Zoom In"
      >
        +
      </button>

      <button
        type="button"
        onClick={handleZoomOut}
        className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-300 hover:text-cyan-200 hover:bg-slate-800/60 transition cursor-pointer font-bold text-sm"
        title="Zoom Out"
      >
        −
      </button>

      <div className="w-[1px] h-4 bg-cyan-500/20 mx-0.5" />

      <button
        type="button"
        onClick={clearMap}
        className="px-2 py-1 rounded-lg text-slate-400 hover:text-rose-300 transition cursor-pointer text-[10px]"
        title="Clear Selected Overlays"
      >
        CLEAR
      </button>
    </div>
  );
}
