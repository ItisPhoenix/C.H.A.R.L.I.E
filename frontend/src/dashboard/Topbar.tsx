import { useEffect, useState, type ReactElement } from "react";
import { GearSixIcon } from "@phosphor-icons/react";
import { useCharlieStore } from "../store/charlie";
import { useLayoutStore } from "./layoutStore";

function useClock(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
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
  const openSettings = useLayoutStore((state) => state.open);

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
        <button type="button" aria-label="Settings" title="Settings" onClick={() => openSettings("settings")}><GearSixIcon aria-hidden="true" weight="light" /></button>
        <span className="hud-clock"><strong className="hud-time">{time}</strong><small className="hud-date">{date}</small></span>
      </div>
    </header>
  );
}
