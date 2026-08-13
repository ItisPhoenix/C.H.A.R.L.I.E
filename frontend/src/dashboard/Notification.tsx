import type { ReactElement } from "react";
import { useCharlieStore } from "../store/charlie";

export function Notification(): ReactElement | null {
  const alert = useCharlieStore((state) => state.activeAlert);
  const dismiss = useCharlieStore((state) => state.dismissAlert);
  if (!alert) return null;

  return (
    <aside className="hud-notification" aria-label="Notification">
      <header><strong>Notification</strong><button type="button" onClick={dismiss} aria-label="Dismiss">Close</button></header>
      <div className="notification-body"><div><small>{alert.severity}</small><strong>{alert.message}</strong></div></div>
    </aside>
  );
}
