"use client";

import type { ReactElement } from "react";
import { useCharlieStore } from "../store/useCharlieStore";

function EmptyState({ text }: { text: string }): ReactElement {
  return (
    <div className="h-40 flex items-center justify-center px-6 text-center text-sm text-[var(--color-text-muted)]">
      {text}
    </div>
  );
}

export function DesktopView(): ReactElement {
  const frame = useCharlieStore((s) => s.latestDesktopFrame);
  const toolActivity = useCharlieStore((s) => s.toolActivity);

  return (
    <div className="space-y-4">
      <div className="rounded-2xl overflow-hidden border border-[var(--color-glass-border)] bg-[var(--color-glass-bg-2)]">
        {frame ? (
          // eslint-disable-next-line @next/next/no-img-element -- base64 data URI, next/image doesn't optimize these
          <img
            src={`data:image/png;base64,${frame.imageB64}`}
            alt="Live view of Charlie's screen"
            className="w-full h-auto block"
          />
        ) : (
          <EmptyState text="No live view yet -- Charlie hasn't looked at the screen this session." />
        )}
      </div>

      <div>
        <p className="text-xs uppercase tracking-widest text-[var(--color-text-muted)] mb-2">
          Action log
        </p>
        {toolActivity.length === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)]">No actions yet.</p>
        ) : (
          <div className="space-y-1.5 max-h-64 overflow-y-auto pr-1 scrollbar">
            {toolActivity
              .slice()
              .reverse()
              .map((e, i) => (
                <div
                  key={i}
                  className="text-[11px] font-mono px-2 py-1.5 rounded-lg bg-black/20 border border-white/5 text-gray-300 break-all"
                >
                  <span className="text-purple-400">{e.name}</span>{" "}
                  <span className="text-gray-500">{e.kind}</span>
                  {e.text && (
                    <span className="block text-gray-400 mt-0.5">{e.text}</span>
                  )}
                </div>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}
