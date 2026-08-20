import type { ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

export interface ProcessEntry {
  name: string;
  pid: number | string;
  status: "RUNNING" | "IDLE" | "QUEUED" | "FAILED" | string;
  uptime?: string;
  cpu?: string | number;
  memory?: string | number;
}

export interface ProcessTelemetryData {
  title?: string;
  subtitle?: string;
  processes?: ProcessEntry[];
}

export function ProcessTelemetryPrimitive({
  primitive,
  data,
}: {
  primitive?: PrimitiveSpec;
  data?: ProcessTelemetryData;
}): ReactElement {
  const telemetryData: ProcessTelemetryData = Array.isArray(data) ? { processes: data } : (data || primitive?.data || {});
  const title = telemetryData.title || "WHAT IS RUNNING";
  const subtitle = telemetryData.subtitle || "LIVE PROCESSES";

  const processes: ProcessEntry[] = Array.isArray(telemetryData.processes)
    ? telemetryData.processes
    : Array.isArray(data)
      ? data
      : [];

  const statusBadge = (st: string) => {
    const s = st.toUpperCase();
    if (s === "RUNNING") {
      return (
        <span className="flex items-center gap-1.5 text-emerald-400 font-semibold text-[10px]">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          RUNNING
        </span>
      );
    }
    if (s === "IDLE") {
      return (
        <span className="flex items-center gap-1.5 text-sky-400 text-[10px]">
          <span className="w-1.5 h-1.5 rounded-full bg-sky-400" />
          IDLE
        </span>
      );
    }
    return (
      <span className="flex items-center gap-1.5 text-amber-400 text-[10px]">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
        {s}
      </span>
    );
  };

  if (processes.length === 0) {
    return (
      <div className="w-full font-mono select-none flex flex-col gap-2 text-left">
        <div>
          <div className="text-xs font-semibold text-cyan-200 tracking-wider uppercase">{title}</div>
          <div className="text-[10px] text-cyan-400/60 uppercase">{subtitle}</div>
        </div>
        <div className="border-y border-cyan-500/10 py-3 text-[11px] text-slate-500 italic">
          NO ACTIVE PROCESSES REPORTED
        </div>
      </div>
    );
  }

  return (
    <div className="w-full font-mono select-none flex flex-col gap-2">
      {/* Header */}
      <div className="text-left">
        <div className="text-xs font-semibold text-cyan-200 tracking-wider uppercase">
          {title}
        </div>
        <div className="text-[10px] text-cyan-400/60 uppercase">
          {subtitle}
        </div>
      </div>

      {/* Table */}
      <div className="p-3 rounded-xl border border-cyan-500/20 bg-slate-950/60 backdrop-blur-md overflow-x-auto">
        <table className="w-full text-left text-[11px] border-collapse">
          <thead>
            <tr className="border-b border-cyan-500/15 text-slate-400 font-medium text-[9px] uppercase tracking-wider">
              <th className="pb-2 pr-4">PROCESS</th>
              <th className="pb-2 pr-4">PID</th>
              <th className="pb-2 pr-4">STATUS</th>
              <th className="pb-2">UPTIME</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-cyan-500/10">
            {processes.map((p, idx) => (
                <tr key={idx} className="hover:bg-cyan-950/20 transition-colors">
                  <td className="py-1.5 pr-4 text-cyan-200 font-medium truncate max-w-[160px]">
                    {p.name}
                  </td>
                  <td className="py-1.5 pr-4 text-slate-400">{p.pid}</td>
                  <td className="py-1.5 pr-4">{statusBadge(p.status)}</td>
                  <td className="py-1.5 text-slate-300">{p.uptime || "—"}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
