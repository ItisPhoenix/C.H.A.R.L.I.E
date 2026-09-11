import { useEffect, useState, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { useCharlieStore } from "../../store/charlie";
import { SpatialMapPrimitive, type SpatialMapData } from "../../composer/primitives/SpatialMapPrimitive";
import { TelemetryGaugesPrimitive, type TelemetryGaugesData } from "../../composer/primitives/TelemetryGaugesPrimitive";
import { ProcessTelemetryPrimitive, type ProcessTelemetryData } from "../../composer/primitives/ProcessTelemetryPrimitive";

export interface SystemLogEntry { timestamp: string; level: "INFO" | "WARN" | "ERROR" | "DEBUG"; message: string; }
export interface ActiveOperationItem { id: string; title: string; subtitle: string; progress: number; status: "RUNNING" | "QUEUED" | "COMPLETED" | "FAILED"; }

function metric(value: number | null | undefined, suffix = ""): string {
  return value === null || value === undefined ? "—" : `${value}${suffix}`;
}

function freshness(updatedAt: string | null, now: number): "FRESH" | "STALE" | "UNAVAILABLE" {
  if (!updatedAt) return "UNAVAILABLE";
  const timestamp = Date.parse(updatedAt);
  if (!Number.isFinite(timestamp)) return "UNAVAILABLE";
  return now - timestamp <= 30_000 ? "FRESH" : "STALE";
}

export function SystemWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const topology = (content.topology || content.network_map) as SpatialMapData | undefined;
  const operations = Array.isArray(content.operations) ? content.operations as ActiveOperationItem[] : [];
  const logs = Array.isArray(content.logs) ? content.logs as SystemLogEntry[] : [];
  const status = useCharlieStore((state) => state.systemStatus);
  const statusUpdatedAt = useCharlieStore((state) => state.systemStatusUpdatedAt);
  const health = useCharlieStore((state) => state.subsystemHealth);
  const healthUpdatedAt = useCharlieStore((state) => state.subsystemHealthUpdatedAt);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!statusUpdatedAt && !healthUpdatedAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [healthUpdatedAt, statusUpdatedAt]);
  const statusFreshness = freshness(statusUpdatedAt, now);
  const healthFreshness = freshness(healthUpdatedAt, now);

  return (
    <div className="charlie-spatial-composition system-composition">
      <header className="spatial-heading system-heading">
        <div className="spatial-kicker">SYSTEM / TASKS WORKSPACE</div>
        <div className="spatial-subtitle">OVERVIEW</div>
      </header>

      <section className="system-live-telemetry absolute left-[2%] right-[2%] top-[7%] z-10" aria-label="Live system telemetry">
        <div className="spatial-kicker">LIVE TELEMETRY <span data-testid="system-telemetry-freshness">[{statusFreshness}]</span></div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 border-y border-cyan-500/15 py-3 mt-2">
          <div><span className="spatial-subtitle">CPU</span><strong>{metric(status?.cpu, "%")}</strong></div>
          <div><span className="spatial-subtitle">RAM</span><strong>{metric(status?.ram, "%")}</strong></div>
          <div><span className="spatial-subtitle">NETWORK</span><strong>{metric(status?.netKbps, " KB/S")}</strong></div>
          <div><span className="spatial-subtitle">DISK</span><strong>{metric(status?.disk, "%")}</strong></div>
        </div>
        <div className="flex flex-wrap gap-3 text-[10px] text-slate-400 mt-2">
          <span>HEALTH [{healthFreshness}]</span>
          {Object.entries(health).length === 0 ? <span>NO SUBSYSTEM SNAPSHOT</span> : Object.entries(health).map(([name, item]) => (
            <span key={name} className={item.status === "degraded" ? "text-amber-300" : item.status === "error" ? "text-rose-300" : "text-cyan-200"}>
              {name.toUpperCase()}: {item.status.toUpperCase()}
            </span>
          ))}
        </div>
      </section>

      <section className="system-topology">
        <div className="spatial-kicker">NETWORK OVERVIEW</div>
        {topology ? <SpatialMapPrimitive data={{ mode: "topology", ...topology }} /> : <div className="spatial-empty">NO AUTHORITATIVE TOPOLOGY REPORTED</div>}
      </section>
      <section className="system-operations">
        <div className="spatial-kicker">TASK STATUS</div>
        {operations.length ? operations.map((op) => (
          <div className="operation-row" key={op.id}>
            <div><strong>{op.title}</strong><span>{op.subtitle}</span></div>
            <em>{op.status}</em>
            {Number.isFinite(op.progress) && op.progress > 0 ? <div className="operation-ring">{op.progress}%</div> : null}
          </div>
        )) : <div className="spatial-empty">NO ACTIVE OPERATIONS REPORTED</div>}
      </section>
      <section className="system-processes"><ProcessTelemetryPrimitive data={content.processes as ProcessTelemetryData} /></section>
      <section className="system-vitals"><TelemetryGaugesPrimitive data={content.vitals as TelemetryGaugesData} /></section>
      <section className="system-feed">
        <div className="spatial-kicker">ACTIVITY FEED</div>
        {logs.length ? logs.slice(0, 10).map((log, i) => (
          <div className="log-row" key={i}><time>{log.timestamp}</time><b className={`log-${log.level.toLowerCase()}`}>[{log.level}]</b><span>{log.message}</span></div>
        )) : <div className="spatial-empty">NO SYSTEM ACTIVITY REPORTED</div>}
      </section>
    </div>
  );
}
