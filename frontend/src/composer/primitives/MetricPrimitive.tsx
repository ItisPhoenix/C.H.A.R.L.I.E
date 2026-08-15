import type { ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

export function MetricPrimitive({ primitive }: { primitive: PrimitiveSpec }): ReactElement {
  const data = primitive.data || {};
  const label = String(data.label ?? "");
  const value = String(data.value ?? "");
  const unit = data.unit ? String(data.unit) : null;
  const trend = data.trend ? String(data.trend) : null; // "up", "down", "neutral"
  const change = data.change ? String(data.change) : null;
  const status = String(data.status ?? "normal"); // normal, success, warning, critical

  const statusColor =
    status === "success"
      ? "text-emerald-400"
      : status === "warning"
        ? "text-amber-400"
        : status === "critical"
          ? "text-rose-400"
          : "text-cyan-300";

  return (
    <div className="p-3 rounded-xl bg-cyan-950/20 border border-cyan-500/20 flex flex-col justify-between min-w-[120px]">
      <span className="text-[10px] font-mono text-slate-400 uppercase tracking-wider mb-1 truncate">
        {label}
      </span>
      <div className="flex items-baseline gap-1.5">
        <span className={`text-base font-bold font-mono tracking-tight ${statusColor}`}>
          {value}
        </span>
        {unit && <span className="text-xs font-mono text-slate-400">{unit}</span>}
      </div>
      {(trend || change) && (
        <div className="flex items-center gap-1 mt-1 text-[10px] font-mono">
          {trend === "up" && <span className="text-emerald-400">↑</span>}
          {trend === "down" && <span className="text-rose-400">↓</span>}
          {change && <span className="text-slate-400">{change}</span>}
        </div>
      )}
    </div>
  );
}
