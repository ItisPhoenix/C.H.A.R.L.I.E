"use client";

import { useEffect, useState, useMemo, type ReactElement } from "react";
import {
  Terminal, Database, Activity, Trash2, ChevronDown, ChevronUp, PanelLeftClose, PanelLeftOpen
} from "lucide-react";
import { useCharlieStore } from "../store/useCharlieStore";

type TabType = "terminal" | "logs";

interface SparklineProps {
  data: number[];
  /** Value range the line is scaled against. Defaults to a 0-100 percent scale (CPU/RAM/GPU usage). */
  min?: number;
  max?: number;
}

export function Sparkline({ data, min = 0, max = 100 }: SparklineProps): ReactElement {
  if (data.length < 2) return <svg className="w-12 h-5" />;
  const width = 100;
  const height = 24;
  const padding = 2;
  const maxVal = max;
  const minVal = min;
  const range = maxVal - minVal || 1;

  const points = data.map((val, i) => {
    const x = (i / (data.length - 1)) * (width - padding * 2) + padding;
    const y = height - ((val - minVal) / range) * (height - padding * 2) - padding;
    return `${x},${y}`;
  }).join(" ");

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-12 h-5 stroke-[1.5] fill-none overflow-visible" style={{ stroke: "var(--color-accent-teal, #06b6d4)" }}>
      <polyline points={points} />
    </svg>
  );
}

export function EventLog(): ReactElement {
  const [activeTab, setActiveTab] = useState<TabType>("terminal");
  const [minimized, setMinimized] = useState(true);
  const [showSystemOverview, setShowSystemOverview] = useState(true);

  const logs = useCharlieStore((s) => s.logs);
  const alerts = useCharlieStore((s) => s.alerts);
  const systemStatus = useCharlieStore((s) => s.systemStatus);
  const toolActivity = useCharlieStore((s) => s.toolActivity);
  const accentColor = useCharlieStore((s) => s.accentColor);

  // Uptime and PID state
  const [uptime, setUptime] = useState(0);
  const [pid, setPid] = useState<number | null>(null);
  const [osHost, setOsHost] = useState("");

  // rolling histories for CPU, RAM, GPU
  const [cpuHist, setCpuHist] = useState<number[]>([]);
  const [ramHist, setRamHist] = useState<number[]>([]);
  const [gpuHist, setGpuHist] = useState<number[]>([]);

  // Telemetry Deltas
  const [cpuDelta, setCpuDelta] = useState("stable");
  const [ramDelta, setRamDelta] = useState("stable");
  const [gpuDelta, setGpuDelta] = useState("stable");

  // Fetch host details
  useEffect(() => {
    async function getHostStatus() {
      try {
        const res = await fetch("/api/status");
        if (res.ok) {
          const data = await res.json();
          if (data.uptime_seconds !== undefined) setUptime(data.uptime_seconds);
          if (data.pid !== undefined) setPid(data.pid);
          if (data.os_host) setOsHost(data.os_host);
        }
      } catch {
        // ignore
      }
    }
    getHostStatus();
    const interval = setInterval(getHostStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  // Update sparklines and deltas
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- accumulates rolling history, needs the previous value
    setCpuHist((prev) => {
      const next = [...prev, systemStatus.cpu].slice(-15);
      if (prev.length > 0) {
        const diff = systemStatus.cpu - prev[prev.length - 1];
        setCpuDelta(diff > 0 ? `+${diff.toFixed(1)}%` : diff < 0 ? `${diff.toFixed(1)}%` : "stable");
      }
      return next;
    });
    setRamHist((prev) => {
      const next = [...prev, systemStatus.ram].slice(-15);
      if (prev.length > 0) {
        const diff = systemStatus.ram - prev[prev.length - 1];
        setRamDelta(diff > 0 ? `+${diff.toFixed(1)}%` : diff < 0 ? `${diff.toFixed(1)}%` : "stable");
      }
      return next;
    });
    setGpuHist((prev) => {
      const next = [...prev, systemStatus.gpu].slice(-15);
      if (prev.length > 0) {
        const diff = systemStatus.gpu - prev[prev.length - 1];
        setGpuDelta(diff > 0 ? `+${diff.toFixed(1)}%` : diff < 0 ? `${diff.toFixed(1)}%` : "stable");
      }
      return next;
    });
  }, [systemStatus]);

  // Format uptime to hh:mm:ss
  const formattedUptime = useMemo(() => {
    const hrs = Math.floor(uptime / 3600);
    const mins = Math.floor((uptime % 3600) / 60);
    const secs = uptime % 60;
    return [
      hrs.toString().padStart(2, "0"),
      mins.toString().padStart(2, "0"),
      secs.toString().padStart(2, "0"),
    ].join(":");
  }, [uptime]);

  // Clear logs action
  const handleClear = () => {
    useCharlieStore.setState({
      logs: [],
      alerts: [],
      toolActivity: [],
    });
  };

  // Render 32px Compact Minimized Console Bar
  if (minimized) {
    return (
      <div className="flex items-center justify-between bg-zinc-950/90 border border-[var(--color-glass-border)] rounded-xl px-4 py-1.5 mx-4 mb-2 select-none text-xs font-mono shadow-xl transition-all">
        <div className="flex items-center gap-4">
          <span className="font-bold text-slate-400 uppercase tracking-widest flex items-center gap-1.5">
            <Terminal className="w-3.5 h-3.5 text-slate-400" />
            CONSOLE (MINIMIZED)
          </span>

          <div className="flex items-center gap-3 border-l border-white/10 pl-3">
            <span className="text-slate-400">CPU: <strong className="text-slate-100">{systemStatus.cpu.toFixed(1)}%</strong></span>
            <span className="text-slate-400">RAM: <strong className="text-slate-100">{systemStatus.ram.toFixed(1)}%</strong></span>
            <span className="text-slate-400">GPU: <strong className="text-slate-100">{systemStatus.gpu.toFixed(1)}%</strong></span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {alerts.length > 0 && (
            <span className="text-amber-400 bg-amber-950/40 px-2 py-0.5 rounded text-xs">
              {alerts.length} ALERTS
            </span>
          )}
          <button
            onClick={() => setMinimized(false)}
            className="flex items-center gap-1 px-2.5 py-1 rounded bg-white/5 hover:bg-white/10 text-slate-200 cursor-pointer font-semibold transition"
          >
            <ChevronUp className="w-3.5 h-3.5 text-slate-400" />
            Expand Console
          </button>
        </div>
      </div>
    );
  }

  // Full Expanded Console View
  return (
    <div className="flex bg-zinc-950 border border-[var(--color-glass-border)] rounded-xl h-60 overflow-hidden mx-4 mb-2 transition-all">
      
      {/* Left: System Overview Dashboard Panel */}
      {showSystemOverview && (
        <div className="w-72 border-r border-[var(--color-glass-border)] p-4 flex flex-col justify-between bg-black/30 shrink-0 font-mono text-xs">
          {/* Resource Sparklines */}
          <div className="space-y-3">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-bold text-slate-500 uppercase tracking-widest flex items-center gap-1.5">
                <Activity className="w-3.5 h-3.5" />
                System Overview
              </h3>
              <button
                onClick={() => setShowSystemOverview(false)}
                className="text-slate-500 hover:text-slate-200 cursor-pointer"
                title="Hide System Overview"
              >
                <PanelLeftClose className="w-3.5 h-3.5" />
              </button>
            </div>
            
            {/* CPU Stats */}
            <div className="flex items-center justify-between">
              <div className="w-20 flex flex-col">
                <span className="text-slate-400 font-semibold">CPU LOAD</span>
                <span className="text-slate-100 text-xs font-bold mt-0.5">{systemStatus.cpu.toFixed(1)}%</span>
              </div>
              <Sparkline data={cpuHist} />
              <span className={`w-12 text-right font-semibold font-mono ${cpuDelta.startsWith("+") ? "text-red-400" : cpuDelta.startsWith("-") ? "text-emerald-400" : "text-slate-500"}`}>
                {cpuDelta}
              </span>
            </div>

            {/* RAM Stats */}
            <div className="flex items-center justify-between">
              <div className="w-20 flex flex-col">
                <span className="text-slate-400 font-semibold">RAM USAGE</span>
                <span className="text-slate-100 text-xs font-bold mt-0.5">{systemStatus.ram.toFixed(1)}%</span>
              </div>
              <Sparkline data={ramHist} />
              <span className={`w-12 text-right font-semibold font-mono ${ramDelta.startsWith("+") ? "text-red-400" : ramDelta.startsWith("-") ? "text-emerald-400" : "text-slate-500"}`}>
                {ramDelta}
              </span>
            </div>

            {/* GPU Stats */}
            <div className="flex items-center justify-between">
              <div className="w-20 flex flex-col">
                <span className="text-slate-400 font-semibold">GPU LOAD</span>
                <span className="text-slate-100 text-xs font-bold mt-0.5">{systemStatus.gpu.toFixed(1)}%</span>
              </div>
              <Sparkline data={gpuHist} />
              <span className={`w-12 text-right font-semibold font-mono ${gpuDelta.startsWith("+") ? "text-red-400" : gpuDelta.startsWith("-") ? "text-emerald-400" : "text-slate-500"}`}>
                {gpuDelta}
              </span>
            </div>
          </div>

          {/* Server Uptime / Host Info */}
          <div className="border-t border-white/5 pt-2.5 space-y-1 text-slate-500 font-mono text-xs uppercase tracking-wide">
            <div className="flex justify-between">
              <span>OS HOST</span>
              <span className="text-slate-300 font-semibold">{osHost || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span>UPTIME</span>
              <span className="text-slate-300 font-semibold">{formattedUptime}</span>
            </div>
            <div className="flex justify-between">
              <span>PROCESS PID</span>
              <span className="text-slate-300 font-semibold">{pid || "OFFLINE"}</span>
            </div>
          </div>
        </div>
      )}

      {/* Right: Multi-Tab Console */}
      <div className="flex-1 flex flex-col h-full bg-zinc-900/30">
        
        {/* Tab Headers bar */}
        <div className="flex items-center justify-between border-b border-[var(--color-glass-border)] px-4 bg-zinc-950/60 select-none shrink-0">
          <div className="flex items-center gap-2">
            {!showSystemOverview && (
              <button
                onClick={() => setShowSystemOverview(true)}
                className="p-1 text-slate-500 hover:text-slate-200 cursor-pointer mr-1"
                title="Show System Overview"
              >
                <PanelLeftOpen className="w-3.5 h-3.5" />
              </button>
            )}

            {[
              { id: "terminal", label: "Terminal", icon: <Terminal className="w-3.5 h-3.5" /> },
              { id: "logs", label: "Logs", icon: <Database className="w-3.5 h-3.5" /> },
            ].map((tab) => {
              const active = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id as TabType)}
                  style={{
                    borderColor: active ? accentColor : "transparent",
                    color: active ? "var(--color-text-primary)" : "var(--color-text-muted)"
                  }}
                  className="flex items-center gap-1.5 px-3 py-2.5 text-xs font-semibold uppercase tracking-wider border-b-2 cursor-pointer transition hover:text-slate-200"
                >
                  {tab.icon}
                  {tab.label}
                </button>
              );
            })}
          </div>

          {/* Actions & Minimize Button */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleClear}
              className="flex items-center gap-1 text-xs uppercase font-bold text-slate-500 hover:text-red-400 cursor-pointer transition font-mono"
              aria-label="Clear Console Output"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Clear
            </button>
            <button
              onClick={() => setMinimized(true)}
              className="p-1 rounded text-slate-500 hover:text-slate-200 hover:bg-white/5 cursor-pointer transition"
              title="Minimize Console"
            >
              <ChevronDown className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Tab Contents Viewport */}
        <div className="flex-1 overflow-y-auto p-4 space-y-1 scrollbar font-mono text-xs leading-relaxed">
          {activeTab === "terminal" && (
            <>
              {toolActivity.length === 0 ? (
                <span className="text-slate-500 italic block">No active terminal commands logged.</span>
              ) : (
                toolActivity.map((t, i) => (
                  <p key={i} className="text-[var(--color-text-primary)] break-all">
                    <span className="text-purple-400 font-bold mr-2 select-none">$</span>
                    <span className="text-slate-400 font-semibold">{t.name}:</span> {t.text}
                  </p>
                ))
              )}
            </>
          )}

          {activeTab === "logs" && (
            <>
              {logs.length === 0 && alerts.length === 0 ? (
                <span className="text-slate-500 italic block">No system logs parsed yet.</span>
              ) : (
                [
                  ...alerts.map((a) => `[${a.timestamp}] [${a.severity.toUpperCase()}] ${a.message}`),
                  ...logs
                ].slice(0, 100).map((line, i) => {
                  let colorClass = "text-slate-400";
                  if (line.includes("[ERROR]")) colorClass = "text-red-400";
                  else if (line.includes("[WARN]") || line.includes("[WARNING]")) colorClass = "text-amber-400";
                  else if (line.includes("[INFO]")) colorClass = "text-cyan-400";
                  return (
                    <p key={i} className={`break-words ${colorClass}`}>
                      {line}
                    </p>
                  );
                })
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
