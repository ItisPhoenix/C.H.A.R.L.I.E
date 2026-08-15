import { useState, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { SpatialMapPrimitive, type SpatialMapData } from "../../composer/primitives/SpatialMapPrimitive";
import { TelemetryGaugesPrimitive, type TelemetryGaugesData } from "../../composer/primitives/TelemetryGaugesPrimitive";
import { ProcessTelemetryPrimitive, type ProcessTelemetryData } from "../../composer/primitives/ProcessTelemetryPrimitive";

export interface SystemLogEntry {
  timestamp: string;
  level: "INFO" | "WARN" | "ERROR" | "DEBUG";
  message: string;
}

export interface ActiveOperationItem {
  id: string;
  title: string;
  subtitle: string;
  progress: number; // 0 to 100
  status: "RUNNING" | "QUEUED" | "COMPLETED" | "FAILED";
}

export function SystemWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const [disclosureLevel, setDisclosureLevel] = useState<1 | 2 | 3>(3);

  const title = String(content.title || workspace.title || "SYSTEM OPERATIONS & DIAGNOSTICS").replace(/^WORKSPACE\s*\/\/\s*/i, "");
  const subtitle = String(content.subtitle || "HARDWARE VITALS // PROCESS TELEMETRY // MESH TOPOLOGY");

  const operations: ActiveOperationItem[] = Array.isArray(content.operations)
    ? (content.operations as ActiveOperationItem[])
    : [];

  const logs: SystemLogEntry[] = Array.isArray(content.logs)
    ? (content.logs as SystemLogEntry[])
    : [];

  const topologyData = (content.topology || content.network_map) as SpatialMapData | undefined;
  const hasTopologyData = Boolean(topologyData && typeof topologyData === "object");

  return (
    <div className="w-full h-full flex flex-col justify-start space-y-6 font-mono select-none text-left p-2 overflow-y-auto pr-4 pb-12">
      {/* 1. Header & Controls */}
      <div className="flex items-start justify-between border-b border-cyan-500/15 pb-2.5">
        <div>
          <div className="text-[11px] text-cyan-400 font-bold tracking-widest uppercase flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
            {title}
          </div>
          <div className="text-[10px] text-cyan-400/60 tracking-widest uppercase mt-0.5">
            {subtitle}
          </div>
        </div>

        {/* Progressive Disclosure Controls */}
        <div className="flex items-center gap-1.5 bg-slate-950/70 border border-cyan-500/20 rounded-lg p-1">
          <button
            type="button"
            onClick={() => setDisclosureLevel(1)}
            className={`px-2.5 py-0.5 text-[10px] rounded transition cursor-pointer ${
              disclosureLevel === 1
                ? "bg-cyan-950 text-cyan-300 border border-cyan-400/50 shadow-sm shadow-cyan-500/20"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Vitals
          </button>
          <button
            type="button"
            onClick={() => setDisclosureLevel(2)}
            className={`px-2.5 py-0.5 text-[10px] rounded transition cursor-pointer ${
              disclosureLevel === 2
                ? "bg-cyan-950 text-cyan-300 border border-cyan-400/50 shadow-sm shadow-cyan-500/20"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Operations
          </button>
          <button
            type="button"
            onClick={() => setDisclosureLevel(3)}
            className={`px-2.5 py-0.5 text-[10px] rounded transition cursor-pointer ${
              disclosureLevel === 3
                ? "bg-cyan-950 text-cyan-300 border border-cyan-400/50 shadow-sm shadow-cyan-500/20"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Full Diagnostic
          </button>
        </div>
      </div>

      {/* 2. Top Section: Dominant Topology Mesh (Left ~60%) & Task Status (Right ~40%) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Left: Subsystem Topology Mesh */}
        {hasTopologyData && disclosureLevel >= 3 && (
          <div className="lg:col-span-7 h-[330px] flex flex-col">
            <SpatialMapPrimitive
              data={{
                mode: "topology",
                title: "MESH SUBSYSTEM TOPOLOGY",
                subtitle: "LIVE TOPOLOGY MAP",
                ...topologyData,
              }}
            />
          </div>
        )}

        {/* Right: Active Task Operations */}
        <div className={hasTopologyData && disclosureLevel >= 3 ? "lg:col-span-5 flex flex-col gap-2.5" : "lg:col-span-12 flex flex-col gap-2.5"}>
          <div className="text-left">
            <div className="text-[11px] font-bold text-cyan-200 tracking-wider uppercase">
              TASK STATUS
            </div>
            <div className="text-[10px] text-cyan-400/60 uppercase">
              ACTIVE OPERATIONS
            </div>
          </div>

          <div className="space-y-2">
            {operations.length === 0 ? (
              <div className="p-3 rounded-xl border border-cyan-500/15 bg-slate-950/40 text-xs text-slate-500 italic">
                No active task operations.
              </div>
            ) : (
              operations.map((op) => {
                const isQueued = op.status === "QUEUED";
                const radius = 14;
                const circumference = 2 * Math.PI * radius;
                const strokeDashoffset = circumference - (op.progress / 100) * circumference;

                return (
                  <div
                    key={op.id}
                    className="p-2.5 rounded-xl border border-cyan-500/15 bg-slate-950/50 backdrop-blur-md flex items-center justify-between gap-3 hover:border-cyan-500/35 transition"
                  >
                    <div className="flex items-center gap-2.5">
                      <div className="w-6 h-6 rounded-lg bg-cyan-950/70 border border-cyan-500/30 flex items-center justify-center text-cyan-300 text-xs">
                        {isQueued ? "◷" : "⚙"}
                      </div>
                      <div>
                        <div className="text-xs font-bold text-slate-100 uppercase tracking-tight">
                          {op.title}
                        </div>
                        <div className="text-[10px] text-slate-400 font-sans">
                          {op.subtitle}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-2.5">
                      <div className="text-right">
                        <div className="text-[9px] text-cyan-400 font-bold uppercase">
                          {op.status}
                        </div>
                        <div className="text-[10px] text-slate-300 font-semibold">
                          {isQueued ? "—" : `${op.progress}%`}
                        </div>
                      </div>

                      {/* Circular Progress Ring */}
                      <div className="relative w-7 h-7 flex items-center justify-center">
                        <svg className="w-full h-full transform -rotate-90" viewBox="0 0 36 36">
                          <circle
                            cx="18"
                            cy="18"
                            r={radius}
                            fill="none"
                            stroke="rgba(0, 240, 255, 0.15)"
                            strokeWidth="2.5"
                          />
                          {!isQueued && (
                            <circle
                              cx="18"
                              cy="18"
                              r={radius}
                              fill="none"
                              stroke="#00f0ff"
                              strokeWidth="2.5"
                              strokeLinecap="round"
                              strokeDasharray={circumference}
                              strokeDashoffset={strokeDashoffset}
                            />
                          )}
                        </svg>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* 3. Bottom Section: Processes, Vitals, Logs (With Safe Margin from Docked Core) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start pt-3 border-t border-cyan-500/15 max-w-[calc(100%-250px)]">
        {/* Left: Live Processes Table */}
        {disclosureLevel >= 2 && (
          <div className="lg:col-span-4">
            <ProcessTelemetryPrimitive
              data={content.processes as ProcessTelemetryData}
            />
          </div>
        )}

        {/* Center: Vitals Gauges & Thermal Stats */}
        <div className={disclosureLevel >= 2 ? "lg:col-span-5" : "lg:col-span-12"}>
          <TelemetryGaugesPrimitive
            data={content.vitals as TelemetryGaugesData}
          />
        </div>

        {/* Right: Activity Log Feed (Terminal Style) */}
        {disclosureLevel >= 3 && (
          <div className="lg:col-span-3 flex flex-col gap-1.5">
            <div className="text-left">
              <div className="text-[11px] font-bold text-cyan-200 tracking-wider uppercase">
                ACTIVITY FEED
              </div>
              <div className="text-[10px] text-cyan-400/60 uppercase">
                SYSTEM LOGS
              </div>
            </div>

            <div className="p-3 rounded-xl border border-cyan-500/15 bg-slate-950/60 backdrop-blur-md h-[180px] overflow-y-auto flex flex-col gap-1 text-[10px] text-left font-mono">
              {logs.length === 0 ? (
                <div className="text-slate-500 italic text-xs py-2">No system logs recorded.</div>
              ) : (
                logs.map((l, idx) => (
                  <div key={idx} className="flex items-start gap-1.5 leading-snug">
                    <span className="text-slate-500">{l.timestamp}</span>
                    <span
                      className={
                        l.level === "WARN"
                          ? "text-amber-400 font-bold"
                          : l.level === "ERROR"
                            ? "text-red-400 font-bold"
                            : "text-cyan-400 font-medium"
                      }
                    >
                      [{l.level}]
                    </span>
                    <span className="text-slate-300 font-sans">{l.message}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
