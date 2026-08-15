import type { ReactElement } from "react";
import type { WidgetInstance } from "../../layout/widgetStore";

export function SystemWidget({ widget }: { widget: WidgetInstance }): ReactElement {
  const content = widget.content || {};
  const metricName = String(content.metric_name || widget.title || "CPU USAGE");
  const value = content.value !== undefined
    ? String(content.value)
    : (widget.summary.match(/\d+%/)?.[0] || widget.summary || "—");

  const temp = content.temperature !== undefined
    ? `${content.temperature}`
    : content.temp !== undefined
      ? `${content.temp}`
      : null;

  const fanSpeed = content.fan_speed !== undefined
    ? String(content.fan_speed)
    : null;

  // Dynamic sparkline data points
  const sparkPoints: number[] = Array.isArray(content.history) && content.history.length > 0
    ? (content.history as number[])
    : [14, 18, 16, 22, 20, 24, 23];

  const maxVal = sparkPoints.length > 0 ? Math.max(...sparkPoints, 1) : 100;
  const minVal = sparkPoints.length > 0 ? Math.min(...sparkPoints, 0) : 0;
  const range = maxVal - minVal || 1;

  const width = 130;
  const height = 40;
  const points = sparkPoints.map((val, idx) => {
    const x = (idx / Math.max(1, sparkPoints.length - 1)) * width;
    const y = height - ((val - minVal) / range) * (height - 8) - 4;
    return { x, y };
  });

  const pathD = points.length > 0
    ? points.reduce(
        (acc, pt, idx) => `${acc} ${idx === 0 ? "M" : "L"} ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`,
        ""
      )
    : "";

  const areaD = pathD ? `${pathD} L ${width} ${height} L 0 ${height} Z` : "";

  return (
    <div className="flex flex-col justify-between h-full font-mono text-left select-none p-1 sm:p-2">
      {/* 1. Header and Large Metric Row */}
      <div className="flex items-end justify-between gap-3">
        <div>
          <div className="text-[10px] text-cyan-400/80 tracking-widest uppercase font-semibold">
            SYSTEM
          </div>
          <div className="text-xs font-bold text-slate-100 uppercase tracking-wider mt-0.5">
            {metricName}
          </div>
          <div className="text-2xl sm:text-3xl font-bold text-cyan-300 tracking-tight mt-1 text-shadow-cyan font-mono">
            {value.includes("%") || isNaN(Number(value)) ? value : `${value}%`}
          </div>
        </div>

        {/* Dynamic Sparkline Chart */}
        {sparkPoints.length > 0 && (
          <div className="w-[130px] h-[40px] relative mb-1">
            <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full overflow-visible">
              <defs>
                <linearGradient id="sys-widget-spark-grad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#22d3ee" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="#22d3ee" stopOpacity="0.0" />
                </linearGradient>
              </defs>
              <line
                x1="0"
                y1={height / 2}
                x2={width}
                y2={height / 2}
                stroke="rgba(34,211,238,0.15)"
                strokeDasharray="2 2"
              />
              <path d={areaD} fill="url(#sys-widget-spark-grad)" />
              <path
                d={pathD}
                fill="none"
                stroke="#22d3ee"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <circle
                cx={points[points.length - 1].x}
                cy={points[points.length - 1].y}
                r="2.5"
                fill="#ffffff"
                stroke="#22d3ee"
                strokeWidth="1.5"
                className="animate-pulse"
              />
            </svg>
          </div>
        )}
      </div>

      {/* 2. Secondary Vitals Telemetry */}
      {(temp !== null || fanSpeed !== null) && (
        <div className="pt-2 border-t border-cyan-500/15 flex flex-col gap-1 text-xs mt-2">
          {temp !== null && (
            <div className="flex justify-between items-center text-slate-300">
              <span className="text-slate-400 text-[11px] font-sans">Core Temperature</span>
              <span className="text-cyan-200 font-semibold">{temp.includes("°") ? temp : `${temp}°C`}</span>
            </div>
          )}
          {fanSpeed !== null && (
            <div className="flex justify-between items-center text-slate-300">
              <span className="text-slate-400 text-[11px] font-sans">Fan Speed</span>
              <span className="text-cyan-200 font-semibold">{fanSpeed.includes("RPM") ? fanSpeed : `${fanSpeed} RPM`}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
