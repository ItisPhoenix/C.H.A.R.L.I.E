"use client";

import { useEffect, useRef, useState, type ReactElement } from "react";
import { X, AlertCircle, AlertTriangle, Info } from "lucide-react";
import { useCharlieStore, type Alert } from "../store/useCharlieStore";

interface ActiveToast extends Alert {
  id: string;
}

const AUTO_DISMISS_MS = 2000;

export function ToastContainer(): ReactElement {
  const alerts = useCharlieStore((s) => s.alerts);
  const [toasts, setToasts] = useState<ActiveToast[]>([]);
  // Track which alerts have already been shown so adding toasts doesn't re-trigger
  const seenRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (alerts.length === 0) return;
    const latest = alerts[0];
    const key = `${latest.message}-${latest.timestamp}`;
    if (seenRef.current.has(key)) return;
    seenRef.current.add(key);

    const newToast: ActiveToast = {
      ...latest,
      id: `${latest.timestamp}-${Math.random()}`,
    };

    setToasts((prev) => [newToast, ...prev].slice(0, 3));

    // Auto-dismiss after 2 seconds (stable: doesn't depend on toasts state)
    const timer = setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== newToast.id));
    }, AUTO_DISMISS_MS);

    return () => clearTimeout(timer);
  }, [alerts]);

  const removeToast = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  if (toasts.length === 0) return <></>;

  return (
    <div className="fixed top-20 right-6 z-50 flex flex-col gap-3 w-80 pointer-events-none select-none">
      {toasts.map((toast) => {
        let borderClass = "border-[var(--color-glass-border)]";
        let textClass = "text-slate-100";
        let icon = <Info className="w-4 h-4 text-cyan-400" />;

        if (toast.severity === "error") {
          borderClass = "border-red-500/50";
          textClass = "text-red-200";
          icon = <AlertCircle className="w-4 h-4 text-red-500" />;
        } else if (toast.severity === "warn") {
          borderClass = "border-amber-500/50";
          textClass = "text-amber-200";
          icon = <AlertTriangle className="w-4 h-4 text-amber-500" />;
        } else {
          borderClass = `border-[var(--color-accent-teal)]/30`;
          icon = <Info className="w-4 h-4 text-[var(--color-accent-teal)]" />;
        }

        return (
          <div
            key={toast.id}
            className={`pointer-events-auto flex items-start gap-3 p-4 rounded-xl border bg-black/85 backdrop-blur-md ${borderClass} shadow-xl animate-[rise_0.25s_ease-out] transition hover:scale-[1.01]`}
          >
            <div className="mt-0.5 shrink-0">{icon}</div>
            <div className="flex-1 min-w-0">
              <p className={`text-xs font-sans leading-relaxed ${textClass} break-words`}>
                {toast.message}
              </p>
              <span className="text-[10px] font-mono text-slate-500 mt-1 block">
                {toast.timestamp}
              </span>
            </div>
            <button
              onClick={() => removeToast(toast.id)}
              className="text-slate-500 hover:text-slate-200 transition shrink-0 p-0.5 rounded-lg hover:bg-white/5 active:scale-95 cursor-pointer"
              aria-label="Close notification"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
