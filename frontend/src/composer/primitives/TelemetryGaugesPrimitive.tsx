import type { ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

export interface GaugeItem {
  id: string;
  label: string;
  value: number; // 0 to 100
  unit?: string;
  color?: string;
}

export interface TelemetryStatPair {
  label: string;
  value: string | number;
}

export interface TelemetryGaugesData {
  title?: string;
  subtitle?: string;
  gauges?: GaugeItem[];
  stats?: TelemetryStatPair[];
}

export function formatGaugeValue(value: number, unit?: string): string {
  const numeric = Number.isFinite(value) ? value : 0;
  switch ((unit || "percent_0_100").toLowerCase()) {
    case "percent_0_100":
      return `${numeric}%`;
    case "fraction_0_1":
      return `${numeric * 100}%`;
    case "celsius":
      return `${numeric}°C`;
    case "bytes": {
      const units = ["B", "KiB", "MiB", "GiB", "TiB"];
      let scaled = numeric;
      let index = 0;
      while (scaled >= 1024 && index < units.length - 1) {
        scaled /= 1024;
        index += 1;
      }
      return `${scaled} ${units[index]}`;
    }
    default:
      return String(numeric);
  }
}

export function TelemetryGaugesPrimitive({
  primitive,
  data,
}: {
  primitive?: PrimitiveSpec;
  data?: TelemetryGaugesData;
}): ReactElement {
  const telemetryData: TelemetryGaugesData = data || primitive?.data || {};
  const title = telemetryData.title || "SYSTEM STATUS";
  const subtitle = telemetryData.subtitle || "VITALS OVERVIEW";

  const gauges: GaugeItem[] = Array.isArray(telemetryData.gauges) ? telemetryData.gauges : [];
  const stats: TelemetryStatPair[] = Array.isArray(telemetryData.stats) ? telemetryData.stats : [];

  return (
    <div className="w-full font-mono select-none flex flex-col gap-2">
      {/* Title & Subtitle */}
      <div className="text-left">
        <div className="text-xs font-semibold text-cyan-200 tracking-wider uppercase">
          {title}
        </div>
        {subtitle && (
          <div className="text-[10px] text-cyan-400/60 uppercase">
            {subtitle}
          </div>
        )}
      </div>

      {/* Main Gauges & Stats Grid (Integrated directly onto canvas) */}
      <div className="py-2 px-1 flex flex-wrap items-center justify-between gap-4 border-b border-cyan-500/15 pb-3">
        {gauges.length === 0 && stats.length === 0 && (
          <div className="text-xs text-slate-500 italic">No telemetry metrics recorded.</div>
        )}

        {/* Circular Gauges */}
        {gauges.length > 0 && (
          <div className="flex items-center gap-4 flex-wrap">
          {gauges.map((g) => {
            const radius = 22;
            const circumference = 2 * Math.PI * radius;
            const strokeDashoffset = circumference - (g.value / 100) * circumference;

            return (
              <div key={g.id} className="flex items-center gap-2.5">
                {/* SVG Radial Gauge */}
                <div className="relative w-12 h-12 flex items-center justify-center">
                  <svg className="w-full h-full transform -rotate-90" viewBox="0 0 56 56">
                    {/* Background Track */}
                    <circle
                      cx="28"
                      cy="28"
                      r={radius}
                      fill="none"
                      stroke="rgba(34, 211, 238, 0.12)"
                      strokeWidth="3.5"
                    />
                    {/* Active Progress */}
                    <circle
                      cx="28"
                      cy="28"
                      r={radius}
                      fill="none"
                      stroke={g.color || "#22d3ee"}
                      strokeWidth="3.5"
                      strokeLinecap="round"
                      strokeDasharray={circumference}
                      strokeDashoffset={strokeDashoffset}
                      style={{ transition: "stroke-dashoffset 0.6s ease" }}
                    />
                  </svg>
                  <span className="absolute text-[11px] font-bold text-slate-100">
                    {g.value}
                  </span>
                </div>

                {/* Label */}
                <div className="text-left">
                  <div className="text-[10px] text-cyan-400/80 font-bold">{g.label}</div>
                  <div className="text-[11px] text-slate-300">
                    {formatGaugeValue(g.value, g.unit)}
                  </div>
                </div>
              </div>
            );
          })}
          </div>
        )}

        {/* Side Vitals Stats List */}
        {stats.length > 0 && (
          <div className="flex items-center gap-5 border-l border-cyan-500/15 pl-4 flex-wrap">
            {stats.map((s, idx) => (
              <div key={idx} className="text-left">
                <div className="text-[9px] text-slate-400 font-medium tracking-wide uppercase">
                  {s.label}
                </div>
                <div className="text-xs font-semibold text-cyan-200 mt-0.5">
                  {s.value}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
