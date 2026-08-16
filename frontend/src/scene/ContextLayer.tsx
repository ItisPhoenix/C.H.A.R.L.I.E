import type { ReactElement } from "react";
import type { PresentationIntent } from "../store/charlie";

interface ContextLayerProps {
  captionText: string | null;
  notifications: PresentationIntent[];
  activeAttention: PresentationIntent | null;
  onDismissIntent?: (id: string) => void;
}

export function ContextLayer({
  captionText,
  notifications,
  activeAttention,
  onDismissIntent,
}: ContextLayerProps): ReactElement {
  return (
    <div className="charlie-context-layer" role="region" aria-label="Contextual Notifications and Captions">
      {/* 1. Near-Core Caption (Anchored lower-center) */}
      {captionText && (
        <div className="charlie-caption-container" role="status" aria-live="polite">
          <div className="charlie-caption-box">
            <span>{captionText}</span>
          </div>
        </div>
      )}

      {/* 2. Contextual Notification Toasts */}
      {notifications.length > 0 && (
        <div className="absolute top-6 right-8 flex flex-col gap-2 z-40 max-w-sm pointer-events-auto">
          {notifications.map((n) => (
            <div
              key={n.id}
              className="p-3 rounded-lg bg-slate-950/90 border border-cyan-400/30 text-left text-xs shadow-lg backdrop-blur-md"
            >
              <div className="flex justify-between items-center text-cyan-300 font-medium mb-1">
                <span>{n.title || "NOTIFICATION"}</span>
                {onDismissIntent && (
                  <button
                    type="button"
                    onClick={() => onDismissIntent(n.id)}
                    className="text-slate-400 hover:text-cyan-200 cursor-pointer ml-2"
                  >
                    ✕
                  </button>
                )}
              </div>
              <p className="text-slate-200">{n.summary}</p>
            </div>
          ))}
        </div>
      )}

      {/* 3. High Attention / Approval Modal */}
      {activeAttention && (
        <div className="charlie-attention-modal-backdrop" role="alertdialog" aria-modal="true">
          <div className="p-6 rounded-2xl bg-slate-950/95 border-2 border-amber-400/60 shadow-2xl max-w-md w-full text-center">
            <div className="w-10 h-10 mx-auto mb-3 rounded-full bg-amber-400/20 border border-amber-400/50 flex items-center justify-center text-amber-300 font-bold">
              !
            </div>
            <h3 className="text-base font-bold text-amber-200 mb-2">{activeAttention.title || "APPROVAL REQUIRED"}</h3>
            <p className="text-xs text-slate-200 mb-6 leading-relaxed">{activeAttention.summary}</p>
            <div className="flex gap-3 justify-center">
              {onDismissIntent && (
                <button
                  type="button"
                  onClick={() => onDismissIntent(activeAttention.id)}
                  className="px-4 py-2 text-xs font-semibold rounded-lg bg-cyan-950/80 border border-cyan-400/40 text-cyan-200 hover:bg-cyan-900 cursor-pointer transition"
                >
                  Acknowledge
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
