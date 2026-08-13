import type { CSSProperties, ReactElement } from "react";
import { useCharlieStore } from "../store/charlie";
import { Panel } from "./Panel";

function Gauge({ label, value }: { label: string; value: number | null }): ReactElement {
  return (
    <div className="monitor-gauge">
      <span>{label}</span>
      <div style={{ "--gauge": `${(value ?? 0) * 3.6}deg` } as CSSProperties}>
        <strong>{value === null ? "—" : `${Math.round(value)}%`}</strong>
      </div>
    </div>
  );
}

function formatUptime(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

export function SystemMonitor(): ReactElement {
  const status = useCharlieStore((state) => state.systemStatus);
  const history = useCharlieStore((state) => state.netHistory);
  const max = Math.max(...history, 1);
  const points = history.length > 1
    ? history.map((value, index) => `${(index / (history.length - 1)) * 100},${22 - (value / max) * 20}`).join(" ")
    : "";

  return (
    <Panel id="system" title="System Monitor">
      <div className="monitor-gauges">
        <Gauge label="CPU" value={status?.cpu ?? null} />
        <Gauge label="RAM" value={status?.ram ?? null} />
        <Gauge label="GPU" value={status?.gpu ?? null} />
        <Gauge label="Disk" value={null} />
      </div>
      <div className="monitor-readout">
        <dl>
          <div><dt>Network</dt><dd>{status?.netKbps !== null && status?.netKbps !== undefined ? `${status.netKbps.toFixed(1)} KB/s` : "Unavailable"}</dd></div>
          <div><dt>Uptime</dt><dd>{status?.uptimeSeconds !== null && status?.uptimeSeconds !== undefined ? formatUptime(status.uptimeSeconds) : "Unavailable"}</dd></div>
          {status?.batteryPercent !== null && status?.batteryPercent !== undefined && <div><dt>Battery</dt><dd className="is-good">{status.batteryPercent}%</dd></div>}
        </dl>
        {points ? <svg viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true"><polyline points={points} /></svg> : <span className="monitor-unavailable">Unavailable</span>}
      </div>
    </Panel>
  );
}
