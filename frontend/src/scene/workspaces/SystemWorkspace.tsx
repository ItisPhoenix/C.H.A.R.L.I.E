import { useEffect, useState, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { useCharlieStore, type RuntimeTask, type RuntimeTruthSubsystem } from "../../store/charlie";
import { TelemetryGaugesPrimitive, type TelemetryGaugesData } from "../../composer/primitives/TelemetryGaugesPrimitive";
import { ProcessTelemetryPrimitive, type ProcessTelemetryData } from "../../composer/primitives/ProcessTelemetryPrimitive";
import "./SystemWorkspace.css";

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

const ACTIVE_TASK_STATUSES = new Set([
  "queued", "planning", "waiting", "running", "paused", "approval_required", "verifying",
]);

function taskTimestamp(task: RuntimeTask): string {
  return task.updatedAt ?? task.completedAt ?? task.createdAt ?? "TIME UNAVAILABLE";
}

function taskTimestampValue(task: RuntimeTask): number {
  const timestamp = Date.parse(taskTimestamp(task));
  return Number.isFinite(timestamp) ? timestamp : -1;
}

function taskStatusClass(status: string): string {
  if (status === "failed") return "log-error";
  if (status === "completed" || status === "cancelled") return "log-info";
  if (status === "running") return "log-active";
  return "log-warn";
}

export function SystemWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const processes = Array.isArray(content.processes) ? content.processes as ProcessTelemetryData : null;
  const vitals = content.vitals && typeof content.vitals === "object" ? content.vitals as TelemetryGaugesData : null;
  const status = useCharlieStore((state) => state.systemStatus);
  const statusUpdatedAt = useCharlieStore((state) => state.systemStatusUpdatedAt);
  const runtimeTruth = useCharlieStore((state) => state.runtimeTruth);
  const health = runtimeTruth?.subsystems ?? {};
  const tasks = useCharlieStore((state) => state.tasks);
  const runtimeObservedAt = runtimeTruth?.observed_at ?? null;
  const taskList = Object.values(tasks);
  const activeTasks = taskList.filter((task) => ACTIVE_TASK_STATUSES.has(task.status));
  const taskActivity = [...taskList].sort((left, right) => taskTimestampValue(right) - taskTimestampValue(left));
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
          <div className="spatial-kicker">SYSTEM / RUNTIME</div>
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
            <div className="spatial-kicker">RUNTIME SUBSYSTEMS</div>
            <div className="system-topology-subtitle">CANONICAL INVENTORY // EDGE MODEL UNAVAILABLE</div>
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
        <div className="system-subsystem-list" aria-label="Canonical runtime subsystem inventory">
          {Object.entries(health).length === 0 ? (
            <div className="spatial-empty">RUNTIME TRUTH UNAVAILABLE</div>
          ) : (
            Object.entries(health).map(([name, item]) => {
              const subsystemState = subsystemStatus(item);
              const detail = typeof item.detail === "string" && item.detail.trim() ? item.detail : null;
              return (
                <div className="system-subsystem-row" key={name} data-health-status={subsystemState}>
                  <div>
                    <strong>{name.toUpperCase()}</strong>
                    {item.required === true && <span className="system-subsystem-required">REQUIRED</span>}
                    {detail && <span>{detail}</span>}
                  </div>
                  <b className={subsystemState === "degraded" || subsystemState === "unavailable" ? "system-health-degraded" : subsystemState === "error" ? "system-health-error" : "system-health-quiet"}>
                    {subsystemState.toUpperCase()}
                  </b>
                </div>
              );
            })
          )}
        </div>
      </section>
      <section className="system-operations">
        <div className="spatial-kicker">ACTIVE TASKS</div>
        {activeTasks.length ? activeTasks.map((task) => (
          <div className="operation-row" key={task.id} data-task-status={task.status}>
            <div><strong>{task.title}</strong><span>{task.origin ? task.origin.toUpperCase() : "TASK"}</span></div>
            <em>{task.status.toUpperCase()}</em>
            {typeof task.progress === "number" ? <div className="operation-ring">{Math.round(task.progress * 100)}%</div> : null}
          </div>
        )) : <div className="spatial-empty">NO CANONICAL ACTIVE TASKS REPORTED</div>}
      </section>
      {(processes || vitals) && (
        <div className="system-host-signals" aria-label="Observed host signals">
          {processes && <section className="system-processes"><ProcessTelemetryPrimitive data={processes} /></section>}
          {vitals && <section className="system-vitals"><TelemetryGaugesPrimitive data={vitals} /></section>}
        </div>
      )}
      <section className="system-feed">
        <div className="spatial-kicker">TASK ACTIVITY</div>
        {taskActivity.length ? taskActivity.slice(0, 10).map((task) => (
          <div className="log-row" key={task.id} data-task-status={task.status}>
            <time>{taskTimestamp(task)}</time>
            <b className={taskStatusClass(task.status)}>[{task.status.toUpperCase()}]</b>
            <span>{task.title}</span>
          </div>
        )) : <div className="spatial-empty">NO CANONICAL TASK ACTIVITY REPORTED</div>}
      </section>
    </div>
  );
}
