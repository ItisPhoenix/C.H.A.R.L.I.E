import type { ReactElement } from "react";
import { useCharlieStore } from "../store/charlie";

export function StatStrip(): ReactElement {
  const status = useCharlieStore((state) => state.systemStatus);
  return (
    <div className="hud-stats" aria-label="Quick system status">
      <span>CPU <b>{status?.cpu !== null && status?.cpu !== undefined ? `${Math.round(status.cpu)}%` : "Unavailable"}</b></span>
      <span>RAM <b>{status?.ram !== null && status?.ram !== undefined ? `${Math.round(status.ram)}%` : "Unavailable"}</b></span>
      <span>NET <b>{status?.netKbps !== null && status?.netKbps !== undefined ? `${status.netKbps.toFixed(1)} KB/s` : "Unavailable"}</b></span>
    </div>
  );
}
