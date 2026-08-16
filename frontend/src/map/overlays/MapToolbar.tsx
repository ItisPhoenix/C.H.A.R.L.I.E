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
    <div className="absolute bottom-6 left-6 z-20 flex items-center gap-1.5 p-1 rounded-xl bg-slate-950/70 border border-cyan-500/20 backdrop-blur-md font-mono text-xs pointer-events-auto shadow-xl">
      <button
        type="button"
        onClick={toggle3D}
        className={`px-2.5 py-1 rounded-lg transition cursor-pointer font-bold ${
          is3D
            ? "bg-cyan-950 text-cyan-200 border border-cyan-400/50 shadow-sm"
            : "text-slate-400 hover:text-slate-200"
        }`}
        title="Toggle 2D / 3D Tilt Mode"
      >
        {is3D ? "3D" : "2D"}
      </button>

      <button
        type="button"
        onClick={handleResetNorth}
        className="px-2 py-1 rounded-lg text-slate-400 hover:text-cyan-300 transition cursor-pointer"
        title="Reset North Up (N)"
      >
        🧭 N
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
