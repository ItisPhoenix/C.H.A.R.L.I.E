import type { ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

interface ChartPoint {
  label: string;
  value: number;
}

export function ChartPrimitive({ primitive }: { primitive: PrimitiveSpec }): ReactElement {
  const data = primitive.data || {};
  const chartType = String(data.chartType ?? "bar"); // bar, line, area
  const points = (Array.isArray(data.data) ? data.data : []) as ChartPoint[];
  const height = Number(data.height ?? 160);
  const title = data.title ? String(data.title) : null;
  const unit = data.unit ? String(data.unit) : "";

  if (!points.length) {
    return <div className="text-xs text-slate-500 italic my-2">No chart data available.</div>;
  }

  const values = points.map((p) => (typeof p.value === "number" ? p.value : 0));
  const maxValue = Math.max(...values, 1);
  const minValue = Math.min(...values, 0);
  const valueRange = maxValue - minValue || 1;

  // Accessible summary string
  const summaryText = `Chart displaying ${points.length} points: ${points.map((p) => `${p.label}: ${p.value}${unit}`).join(", ")}`;

  // Bar Chart
  if (chartType === "bar") {
    return (
      <div className="w-full my-3" role="img" aria-label={summaryText}>
        {title && <h4 className="text-xs font-semibold text-cyan-300 mb-2">{title}</h4>}
        <div className="flex items-end gap-3 h-36 pt-4 pb-1 border-b border-cyan-500/20 px-2 bg-slate-950/40 rounded-t-lg">
          {points.map((p, idx) => {
            const heightPercent = Math.max(8, (p.value / maxValue) * 100);
            return (
              <div key={idx} className="flex-1 flex flex-col items-center gap-1.5 h-full justify-end group">
                <span className="text-[10px] font-mono text-cyan-300 opacity-80 group-hover:opacity-100 transition-opacity">
                  {p.value}
                  {unit}
                </span>
                <div
                  className="w-full max-w-[48px] rounded-t bg-cyan-400/80 group-hover:bg-cyan-300 transition-colors shadow-lg shadow-cyan-500/20"
                  style={{ height: `${heightPercent}%` }}
                />
                <span className="text-[10px] font-mono text-slate-400 truncate max-w-[64px] text-center">
                  {p.label}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  // Line / Area SVG Chart
  const svgWidth = 400;
  const svgHeight = height;
  const paddingX = 30;
  const paddingY = 20;

  const chartW = svgWidth - paddingX * 2;
  const chartH = svgHeight - paddingY * 2;

  const pointsCoordinates = points.map((p, idx) => {
    const x = paddingX + (idx / Math.max(1, points.length - 1)) * chartW;
    const y = svgHeight - paddingY - ((p.value - minValue) / valueRange) * chartH;
    return { x, y, ...p };
  });

  const pathD = pointsCoordinates.reduce(
    (acc, curr, idx) => `${acc} ${idx === 0 ? "M" : "L"} ${curr.x} ${curr.y}`,
    ""
  );

  const areaD = `${pathD} L ${pointsCoordinates[pointsCoordinates.length - 1]?.x ?? 0} ${
    svgHeight - paddingY
  } L ${pointsCoordinates[0]?.x ?? 0} ${svgHeight - paddingY} Z`;

  return (
    <div className="w-full my-3" role="img" aria-label={summaryText}>
      {title && <h4 className="text-xs font-semibold text-cyan-300 mb-2">{title}</h4>}
      <div className="w-full overflow-hidden rounded-xl border border-cyan-500/20 bg-slate-950/50 p-2">
        <svg viewBox={`0 0 ${svgWidth} ${svgHeight}`} className="w-full h-auto">
          {/* Subtle horizontal grid lines */}
          <line
            x1={paddingX}
            y1={paddingY}
            x2={svgWidth - paddingX}
            y2={paddingY}
            stroke="rgba(34,211,238,0.1)"
            strokeDasharray="3 3"
          />
          <line
            x1={paddingX}
            y1={svgHeight / 2}
            x2={svgWidth - paddingX}
            y2={svgHeight / 2}
            stroke="rgba(34,211,238,0.1)"
            strokeDasharray="3 3"
          />
          <line
            x1={paddingX}
            y1={svgHeight - paddingY}
            x2={svgWidth - paddingX}
            y2={svgHeight - paddingY}
            stroke="rgba(34,211,238,0.25)"
          />

          {/* Area Fill */}
          {chartType === "area" && (
            <path d={areaD} fill="rgba(34,211,238,0.12)" />
          )}

          {/* Line Path */}
          <path
            d={pathD}
            fill="none"
            stroke="#22d3ee"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Data Points */}
          {pointsCoordinates.map((pt, idx) => (
            <g key={idx} className="group">
              <circle
                cx={pt.x}
                cy={pt.y}
                r="4"
                fill="#020617"
                stroke="#22d3ee"
                strokeWidth="2"
                className="hover:r-6 transition-all cursor-pointer"
              />
              <text
                x={pt.x}
                y={svgHeight - 4}
                fill="#94a3b8"
                fontSize="9"
                fontFamily="monospace"
                textAnchor="middle"
              >
                {pt.label}
              </text>
            </g>
          ))}
        </svg>
      </div>
    </div>
  );
}
