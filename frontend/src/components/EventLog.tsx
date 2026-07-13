"use client";

import { useState } from "react";
import type { ReactElement } from "react";
import { useCharlieStore, lighten } from "@/store/useCharlieStore";

const SEVERITY_COLOR: Record<string, string> = {
  warning: "#f59e0b",
  error: "#ef4444",
};

export function EventLog(): ReactElement {
  const [open, setOpen] = useState(false);
  const logs = useCharlieStore((s) => s.logs);
  const alerts = useCharlieStore((s) => s.alerts);
  const accentColor = useCharlieStore((s) => s.accentColor);

  const infoColor = lighten(accentColor, 0.35);

  // Combine alerts (newest last) and recent logs into a single stream.
  const lines = [
    ...alerts.map(
      (a) => `${a.timestamp} [${a.severity}] ${a.message}`
    ),
    ...logs,
  ].slice(-40);
  const unread = alerts.length + logs.length;

  return (
    <div className="glass rounded-2xl overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center justify-between px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] cursor-pointer transition"
      >
        <span className="font-display tracking-wide">
          Event Log{unread > 0 ? ` (${unread})` : ""}
        </span>
        <span className="text-xs">{open ? "Hide" : "Show"}</span>
      </button>
      {open && (
        <div className="max-h-48 overflow-y-auto px-4 pb-3 space-y-1 scrollbar border-t border-[var(--color-glass-border)]">
          {lines.length === 0 ? (
            <p className="text-xs text-[var(--color-text-muted)] py-2">
              No events yet.
            </p>
          ) : (
            lines.map((line, i) => {
              const alertMatch = line.match(/^(\S+) \[(\w+)\]/);
              let colorStyle = { color: "#d1d5db" };
              if (alertMatch) {
                const severity = alertMatch[2];
                if (severity === "info") {
                  colorStyle = { color: infoColor };
                } else if (SEVERITY_COLOR[severity]) {
                  colorStyle = { color: SEVERITY_COLOR[severity] };
                }
              } else {
                colorStyle = { color: infoColor };
              }
              return (
                <p
                  key={i}
                  style={colorStyle}
                  className="text-xs font-mono leading-relaxed"
                >
                  {line}
                </p>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
