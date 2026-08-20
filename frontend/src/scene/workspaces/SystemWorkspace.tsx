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

  const title = String(content.title || workspace.title || "MACHINE DIAGNOSTICS & SYSTEM TELEMETRY").replace(/^WORKSPACE\s*\/\/\s*/i, "");
  const subtitle = String(content.subtitle || "ACTIVE RUNTIME // HOST HEALTH // PROCESS MESH");

  const operations: ActiveOperationItem[] = Array.isArray(content.operations)
    ? (content.operations as ActiveOperationItem[])
    : [];

  const logs: SystemLogEntry[] = Array.isArray(content.logs)
    ? (content.logs as SystemLogEntry[])
    : [];

  const topologyData = (content.topology || content.network_map) as SpatialMapData | undefined;
  const hasTopology = Boolean(topologyData && typeof topologyData === "object");

  return (
    <div className="w-full h-full flex flex-col justify-start space-y-6 font-mono select-none text-left p-2 sm:p-4 overflow-y-auto pr-4 pb-16">
      {/* 1. Header & Diagnostics Mode Selector */}
      <div className="flex items-start justify-between border-b border-cyan-500/15 pb-3">
        <div>
          <div className="text-xs text-cyan-400 font-bold tracking-widest uppercase flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
            <span>{title}</span>
          </div>
          <div className="text-[10px] text-cyan-400/60 tracking-widest uppercase mt-0.5">
            {subtitle}
          </div>
        </div>

        {/* Mode Toggle */}
        <div className="flex items-center gap-1 bg-slate-950/80 border border-cyan-500/20 rounded-lg p-0.5">
          <button
            type="button"
            onClick={() => setDisclosureLevel(1)}
            className={`px-2.5 py-1 text-[10px] rounded transition cursor-pointer font-mono ${
              disclosureLevel === 1
                ? "bg-cyan-950 text-cyan-300 border border-cyan-400/50 shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Vitals
          </button>
          <button
            type="button"
            onClick={() => setDisclosureLevel(2)}
            className={`px-2.5 py-1 text-[10px] rounded transition cursor-pointer font-mono ${
              disclosureLevel === 2
                ? "bg-cyan-950 text-cyan-300 border border-cyan-400/50 shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Processes
          </button>
          <button
            type="button"
            onClick={() => setDisclosureLevel(3)}
            className={`px-2.5 py-1 text-[10px] rounded transition cursor-pointer font-mono ${
              disclosureLevel === 3
                ? "bg-cyan-950 text-cyan-300 border border-cyan-400/50 shadow-sm"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Full Matrix
          </button>
        </div>
      </div>

      {/* 2. Top Section: compact metrics when an authoritative topology is available */}
      {!hasTopology && (
        <div className="w-full">
          <TelemetryGaugesPrimitive data={content.vitals as TelemetryGaugesData} />
        </div>
      )}

      {/* 3. Middle Two-Column Stream: Processes Stream (Left) + Active Operations (Right) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {hasTopology && (
          <div className="lg:col-span-8 h-[430px] flex flex-col overflow-hidden bg-transparent relative charlie-map-immersive">
            <SpatialMapPrimitive
              data={{
                mode: "topology",
                title: "RUNTIME SUBSYSTEM TOPOLOGY",
                subtitle: "AUTHORITATIVE SUBSYSTEM INVENTORY",
                ...topologyData,
              }}
            />
          </div>
        )}
        {/* Left: Process telemetry when topology is not available */}
        {!hasTopology && (
          <div className="lg:col-span-6 space-y-4">
            <ProcessTelemetryPrimitive
              data={content.processes as ProcessTelemetryData}
            />
          </div>
        )}

        {/* Right: Active System Operations (Compact rows with hairlines) */}
        <div className={`${hasTopology ? "lg:col-span-4" : "lg:col-span-6"} space-y-2`}>
          {hasTopology && <TelemetryGaugesPrimitive data={content.vitals as TelemetryGaugesData} />}
          <div className="text-[10px] font-bold text-cyan-400 uppercase tracking-widest">
            ACTIVE SYSTEM OPERATIONS
          </div>

          <div className="divide-y divide-cyan-500/10 border-y border-cyan-500/10 py-1">
            {operations.length === 0 ? (
              <div className="py-2 text-xs text-slate-500 italic">
                NO ACTIVE OPERATIONS REPORTED
              </div>
            ) : (
              operations.map((op) => {
                const isRunning = op.status === "RUNNING";
                return (
                  <div
                    key={op.id}
                    className="py-2.5 px-1 space-y-1.5 hover:bg-cyan-950/20 transition rounded"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-xs font-bold text-slate-100 font-sans">
                        {op.title}
                      </div>
                      <span
                        className={`text-[9px] px-1.5 py-0.2 rounded font-mono font-bold ${
                          isRunning
                            ? "bg-cyan-950 text-cyan-300 border border-cyan-500/40"
                            : "bg-slate-900 text-slate-400 border border-slate-700"
                        }`}
                      >
                        {op.status}
                      </span>
                    </div>

                    <div className="text-[11px] text-slate-400 font-sans">
                      {op.subtitle}
                    </div>

                    {/* Clean Linear Progress Bar */}
                    <div className="w-full bg-slate-900/80 rounded-full h-1 overflow-hidden">
                      <div
                        className="bg-cyan-400 h-full transition-all duration-300"
                        style={{ width: `${Math.min(100, Math.max(0, op.progress))}%` }}
                      />
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {hasTopology && (
        <div className="max-w-[calc(100%-250px)]">
          <ProcessTelemetryPrimitive data={content.processes as ProcessTelemetryData} />
        </div>
      )}

      {/* 4. Bottom Diagnostic Log Rail (Low-profile terminal surface) */}
      <div className="space-y-2 pt-2 border-t border-cyan-500/15 max-w-[calc(100%-250px)]">
        <div className="text-[10px] font-bold text-cyan-400 uppercase tracking-widest">
          DIAGNOSTIC LOG STREAM
        </div>
        <div className="p-3 rounded-lg border border-cyan-500/15 bg-slate-950/70 font-mono text-[11px] max-h-36 overflow-y-auto space-y-1 text-left">
          {logs.length === 0 ? (
            <div className="text-slate-500 italic text-xs py-1">
              // NO DIAGNOSTIC ACTIVITY REPORTED
            </div>
          ) : (
            logs.map((log, idx) => (
              <div key={idx} className="flex items-start gap-2 leading-relaxed">
                <span className="text-slate-500">{log.timestamp}</span>
                <span
                  className={`font-bold ${
                    log.level === "ERROR"
                      ? "text-red-400"
                      : log.level === "WARN"
                        ? "text-amber-400"
                        : "text-cyan-400"
                  }`}
                >
                  [{log.level}]
                </span>
                <span className="text-slate-300 font-sans">{log.message}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
