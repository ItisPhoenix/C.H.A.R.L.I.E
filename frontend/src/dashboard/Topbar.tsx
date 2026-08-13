import { useEffect, useState, type ReactElement } from "react";
import { useCharlieStore } from "../store/charlie";

function useClock(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

function IconButton({ label, path }: { label: string; path: string }): ReactElement {
  return (
    <button type="button" aria-label={label} title={label}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d={path} strokeLinecap="round" strokeLinejoin="round" /></svg>
    </button>
  );
}

export function Topbar(): ReactElement {
  const coreState = useCharlieStore((state) => state.coreState);
  const connected = useCharlieStore((state) => state.connected);
  const subsystemHealth = useCharlieStore((state) => state.subsystemHealth);
  const now = useClock();
  const time = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  const date = now.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
  const status = connected ? coreState : "offline";
  const healthValues = Object.values(subsystemHealth);
  const health = healthValues.length === 0
    ? "Unknown"
    : healthValues.some((item) => item.status === "degraded" || item.status === "stopped") ? "Degraded" : "Healthy";
  const voice = subsystemHealth.voice?.detail ?? "Unknown";

  return (
    <header className="hud-topbar">
      <div className="brand-lockup">
        <span className="brand-orbit" aria-hidden="true" />
        <span>
          <strong className="brand-name">CHARLIE</strong>
          <small className="brand-subtitle">AGENTIC OS</small>
        </span>
      </div>

      <div className="hud-health" aria-label="System status">
        <span><i className="health-dot" /> Status: {status}</span>
        <span><i className="health-crosshair" /> System health: <b>{health}</b></span>
        <span><i className="health-wave" /> Voice: <b>{voice}</b></span>
      </div>

      <div className="hud-utilities">
        <IconButton label="Search" path="M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm10 17-5-5" />
        <IconButton label="Messages" path="M4 5h16v11H8l-4 4V5Z" />
        <IconButton label="Settings" path="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm0-5 1.4 2.1 2.5.3.7 2.4 2 1.5-1 2.3 1 2.3-2 1.5-.7 2.4-2.5.3-1.4 2.1-2.3-1-2.3 1-1.4-2.1-2.5-.3-.7-2.4-2-1.5 1-2.3-1-2.3 2-1.5.7-2.4 2.5-.3L12 3.5Z" />
        <span className="hud-clock"><strong className="hud-time">{time}</strong><small className="hud-date">{date}</small></span>
      </div>
    </header>
  );
}
