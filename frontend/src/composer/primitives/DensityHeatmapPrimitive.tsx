import { useState, type ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

export interface HeatmapPoint {
  x: number;
  y: number;
  value: number; // 0 to 1
}

export interface DensityHeatmapData {
  title?: string;
  subtitle?: string;
  gridWidth?: number;
  gridHeight?: number;
  points?: HeatmapPoint[];
  minLabel?: string;
  maxLabel?: string;
}

export function DensityHeatmapPrimitive({ primitive, data }: { primitive?: PrimitiveSpec; data?: DensityHeatmapData }): ReactElement {
  const heatmapData: DensityHeatmapData = data || primitive?.data || {};
  const title = heatmapData.title || "ACTIVITY DENSITY";
  const subtitle = heatmapData.subtitle || "PAST 72 HOURS";
  const cols = heatmapData.gridWidth || 20;
  const rows = heatmapData.gridHeight || 10;
  const [hoveredCell, setHoveredCell] = useState<{ x: number; y: number; val: number } | null>(null);

  // Calculate matrix strictly from incoming points data
  const matrix: number[][] = Array.from({ length: rows }, (_, r) =>
    Array.from({ length: cols }, (_, c) => {
      if (Array.isArray(heatmapData.points)) {
        const found = heatmapData.points.find((p) => p.x === c && p.y === r);
        if (found) return found.value;
        return 0;
      }
      return 0;
    })
  );

  // Map intensity 0..1 to color
  const cellColor = (val: number): string => {
    if (val < 0.15) return "rgba(10, 30, 48, 0.4)";
    if (val < 0.35) return "rgba(14, 80, 115, 0.6)";
    if (val < 0.6) return "rgba(34, 211, 238, 0.8)";
    if (val < 0.8) return "rgba(251, 146, 60, 0.9)";
    return "rgba(248, 113, 113, 1)";
  };

  return (
    <div className="w-full font-mono select-none flex flex-col gap-2">
      {/* Title & Subtitle */}
      <div className="flex items-center justify-between text-left">
        <div>
          <div className="text-xs font-semibold text-cyan-200 tracking-wider uppercase">
            {title}
          </div>
          <div className="text-[10px] text-cyan-400/60 uppercase">
            {subtitle}
          </div>
        </div>
        {hoveredCell && (
          <div className="text-[10px] text-cyan-300">
            [X:{hoveredCell.x} Y:{hoveredCell.y} // {(hoveredCell.val * 100).toFixed(0)}%]
          </div>
        )}
      </div>

      {/* Grid Canvas */}
      <div className="w-full p-2.5 rounded-xl border border-cyan-500/20 bg-slate-950/60 backdrop-blur-md">
        <div
          className="grid gap-[2px] w-full"
          style={{
            gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
            gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))`,
          }}
        >
          {matrix.map((row, rIdx) =>
            row.map((val, cIdx) => (
              <div
                key={`${rIdx}-${cIdx}`}
                className="aspect-square rounded-[2px] transition-colors hover:ring-1 hover:ring-cyan-300 cursor-crosshair"
                style={{ backgroundColor: cellColor(val) }}
                onMouseEnter={() => setHoveredCell({ x: cIdx, y: rIdx, val })}
                onMouseLeave={() => setHoveredCell(null)}
              />
            ))
          )}
        </div>

        {/* Gradient Legend */}
        <div className="flex items-center justify-between mt-2 pt-2 border-t border-cyan-500/10 text-[9px] text-cyan-400/70">
          <span>{heatmapData.minLabel || "LOW"}</span>
          <div className="flex-1 mx-3 h-1.5 rounded-full bg-gradient-to-r from-[#0a1e30] via-[#22d3ee] via-[#fb923c] to-[#f87171]" />
          <span>{heatmapData.maxLabel || "HIGH"}</span>
        </div>
      </div>
    </div>
  );
}
