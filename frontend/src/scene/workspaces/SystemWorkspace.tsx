import { useEffect, useState, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { useCharlieStore, type RuntimeTruthSubsystem } from "../../store/charlie";
import { SpatialMapPrimitive, type SpatialMapData } from "../../composer/primitives/SpatialMapPrimitive";
import { TelemetryGaugesPrimitive, type TelemetryGaugesData } from "../../composer/primitives/TelemetryGaugesPrimitive";
import { ProcessTelemetryPrimitive, type ProcessTelemetryData } from "../../composer/primitives/ProcessTelemetryPrimitive";
import "./SystemWorkspace.css";

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

function subsystemStatus(value: RuntimeTruthSubsystem): string {
  return typeof value.status === "string" && value.status ? value.status : "unknown";
}

export function SystemWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const topology = (content.topology || content.network_map) as SpatialMapData | undefined;
  const operations = Array.isArray(content.operations) ? content.operations as ActiveOperationItem[] : [];
  const logs = Array.isArray(content.logs) ? content.logs as SystemLogEntry[] : [];
  const processes = Array.isArray(content.processes) ? content.processes as ProcessTelemetryData : null;
  const vitals = content.vitals && typeof content.vitals === "object" ? content.vitals as TelemetryGaugesData : null;
  const status = useCharlieStore((state) => state.systemStatus);
  const statusUpdatedAt = useCharlieStore((state) => state.systemStatusUpdatedAt);
  const runtimeTruth = useCharlieStore((state) => state.runtimeTruth);
  const health = runtimeTruth?.subsystems ?? {};
  const runtimeObservedAt = runtimeTruth?.observed_at ?? null;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!statusUpdatedAt && !runtimeObservedAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [runtimeObservedAt, statusUpdatedAt]);
  const statusFreshness = freshness(statusUpdatedAt, now);
  const healthFreshness = freshness(runtimeObservedAt, now);

  return (
    <div className="charlie-spatial-composition system-composition">
      <header className="spatial-heading system-heading">
        <div className="system-heading-line">
          <div className="spatial-kicker">SYSTEM / RUNTIME TOPOLOGY</div>
          <div
            className="system-runtime-state"
            data-runtime-health={runtimeTruth?.status ?? "unavailable"}
            data-runtime-authority={runtimeTruth?.authority ?? "unavailable"}
            data-runtime-revision={runtimeTruth?.revision ?? "unavailable"}
          >
            {runtimeTruth ? `RUNTIME ${runtimeTruth.status.toUpperCase()}` : "RUNTIME UNAVAILABLE"}
          </div>
        </div>
        <div className="spatial-subtitle">THE MACHINE / AUTHORITATIVE STATE</div>
      </header>

      <section className="system-topology">
        <div className="system-topology-heading">
          <div>
            <div className="spatial-kicker">RUNTIME TOPOLOGY</div>
            <div className="system-topology-subtitle">AUTHORITATIVE SUBSYSTEM LINKS</div>
          </div>
          <div className="system-topology-meta">
            <div className="system-topology-health" aria-label={`Subsystem health [${healthFreshness}]`}>
              <span>HEALTH [{healthFreshness}]</span>
              {Object.entries(health).length === 0 ? <span>RUNTIME TRUTH UNAVAILABLE</span> : Object.entries(health).map(([name, item]) => {
                const subsystemState = subsystemStatus(item);
                return (
                  <span key={name} data-health-status={subsystemState} className={subsystemState === "degraded" ? "system-health-degraded" : subsystemState === "error" ? "system-health-error" : "system-health-quiet"}>
                    {name.toUpperCase()}: {subsystemState.toUpperCase()}
                  </span>
                );
              })}
            </div>
            <section className="system-live-telemetry" aria-label="Live system telemetry">
              <div className="spatial-kicker">LIVE VALUES <span data-testid="system-telemetry-freshness">[{statusFreshness}]</span></div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div><span className="spatial-subtitle">CPU</span><strong>{metric(status?.cpu, "%")}</strong></div>
                <div><span className="spatial-subtitle">RAM</span><strong>{metric(status?.ram, "%")}</strong></div>
                <div><span className="spatial-subtitle">NETWORK</span><strong>{metric(status?.netKbps, " KB/S")}</strong></div>
                <div><span className="spatial-subtitle">DISK</span><strong>{metric(status?.disk, "%")}</strong></div>
              </div>
            </section>
          </div>
        </div>
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
      {(processes || vitals) && (
        <div className="system-host-signals" aria-label="Observed host signals">
          {processes && <section className="system-processes"><ProcessTelemetryPrimitive data={processes} /></section>}
          {vitals && <section className="system-vitals"><TelemetryGaugesPrimitive data={vitals} /></section>}
        </div>
      )}
      <section className="system-feed">
        <div className="spatial-kicker">ACTIVITY FEED</div>
        {logs.length ? logs.slice(0, 10).map((log, i) => (
          <div className="log-row" key={i}><time>{log.timestamp}</time><b className={`log-${log.level.toLowerCase()}`}>[{log.level}]</b><span>{log.message}</span></div>
        )) : <div className="spatial-empty">NO SYSTEM ACTIVITY REPORTED</div>}
      </section>
    </div>
  );
}
