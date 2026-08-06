"use client";

import { useEffect, useRef, useState, type ReactElement } from "react";
import { Monitor } from "lucide-react";
import { useCharlieStore } from "../store/useCharlieStore";

function formatAge(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 1) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  return `${m}m ago`;
}

/** Real display of the last-captured desktop frame -- event-driven, throttled, not a continuous video feed (see charlie/tools.py:_emit_desktop_frame). */
export function DesktopFrameView(): ReactElement {
  const frame = useCharlieStore((s) => s.latestDesktopFrame);
  const [now, setNow] = useState(() => Date.now());
  const imgRef = useRef<HTMLImageElement | null>(null);
  // Marks are in the captured (natural) image's pixel space; the <img> renders
  // scaled to fit its container, so overlay positions must scale by the same
  // ratio or they drift off the actual UI elements as the panel resizes.
  const [scale, setScale] = useState(1);

  // Re-render every second so the "Xs ago" freshness label stays live.
  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  const updateScale = () => {
    const img = imgRef.current;
    if (img && img.naturalWidth > 0) setScale(img.clientWidth / img.naturalWidth);
  };

  useEffect(() => {
    window.addEventListener("resize", updateScale);
    return () => window.removeEventListener("resize", updateScale);
  }, []);

  return (
    <div className="flex-1 bg-zinc-950 p-6 flex flex-col overflow-y-auto scrollbar anim-rise">
      <div className="border-b border-white/5 pb-3 mb-6 flex items-center justify-between">
        <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
          <Monitor className="w-5 h-5 text-slate-400" />
          Desktop Frame
        </h2>
        {frame && (
          <span className="text-xs font-mono text-slate-500 uppercase tracking-widest">
            last updated {formatAge(now - frame.receivedAt)}
          </span>
        )}
      </div>

      {!frame ? (
        <div className="flex-1 grid place-items-center text-center">
          <div className="space-y-2">
            <Monitor className="w-8 h-8 text-slate-700 mx-auto" />
            <p className="text-sm text-slate-500">No frame captured yet.</p>
            <p className="text-xs text-slate-600 max-w-sm">
              Frames arrive only when a desktop tool actually runs (click, screenshot,
              read-screen) -- this is event-driven, not a continuous video feed.
            </p>
          </div>
        </div>
      ) : (
        <div className="relative inline-block max-w-full mx-auto rounded-xl overflow-hidden border border-white/10">
          <img
            ref={imgRef}
            src={`data:image/png;base64,${frame.imageB64}`}
            alt="Last captured desktop frame"
            className="block max-w-full h-auto"
            onLoad={updateScale}
          />
          {frame.marks.map((mark) => {
            const [x0, y0, x1, y1] = mark.bounds;
            return (
              <div
                key={mark.mark_id}
                className="absolute border border-status-listening/70 bg-status-listening/10"
                style={{ left: x0 * scale, top: y0 * scale, width: (x1 - x0) * scale, height: (y1 - y0) * scale }}
                title={mark.name}
              >
                <span className="absolute -top-4 left-0 text-[9px] font-mono text-status-listening bg-black/70 px-1 rounded">
                  {mark.mark_id}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
