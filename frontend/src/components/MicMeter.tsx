"use client";

import type { ReactElement } from "react";
import { useCharlieStore } from "../store/useCharlieStore";

export function MicMeter(): ReactElement {
  const level = useCharlieStore((s) => s.audioLevel);
  const pct = Math.max(0, Math.min(100, Math.round(level * 100)));
  return (
    <div className="flex items-center gap-2" aria-label="mic level">
      <span className="text-xs text-[var(--color-text-secondary)]">MIC</span>
      <div
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-1.5 w-24 rounded-full bg-[var(--color-glass-border)] overflow-hidden"
      >
        <div
          className="h-full bg-[var(--color-accent)] transition-[width] duration-100"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
