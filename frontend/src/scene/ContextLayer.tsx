import { useEffect, type ReactElement } from "react";
import type { PresentationIntent } from "../store/charlie";
import type { VisualRuntimeState } from "../runtime/visualRuntime";
import { useModalFocus } from "../components/useModalFocus";

interface ContextLayerProps {
  captionText: string | null;
  notifications: PresentationIntent[];
  activeAttention: PresentationIntent | null;
  visualRuntime: VisualRuntimeState;
  onDismissIntent?: (id: string) => void;
  onClearVisualRuntime?: (expectedUpdatedAt?: string) => void;
}

export function ContextLayer({
  captionText,
  notifications,
  activeAttention,
  visualRuntime,
  onDismissIntent,
  onClearVisualRuntime,
}: ContextLayerProps): ReactElement {
  const attentionRef = useModalFocus<HTMLDivElement>(Boolean(activeAttention), () => {
    if (activeAttention && onDismissIntent && typeof activeAttention.content.request_id !== "string") {
      onDismissIntent(activeAttention.id);
    }
  });
  useEffect(() => {
    if (!visualRuntime.expiresAt || !onClearVisualRuntime) return;
    const expectedUpdatedAt = visualRuntime.updatedAt;
    const timeout = window.setTimeout(
      () => onClearVisualRuntime(expectedUpdatedAt),
      Math.max(0, visualRuntime.expiresAt - Date.now()),
    );
    return () => window.clearTimeout(timeout);
  }, [visualRuntime.expiresAt, visualRuntime.updatedAt, onClearVisualRuntime]);

  const runtimeDominant = [
    "transcribing",
    "thinking",
    "acting",
    "approval_wait",
    "success",
    "error",
    "recovering",
    "degraded",
    "offline",
  ].includes(visualRuntime.phase);
  const primaryNotification = [...notifications].sort((left, right) => right.priority - left.priority)[0] ?? null;
  const contextKind = activeAttention
    ? "attention"
    : runtimeDominant
      ? "runtime"
      : captionText
        ? "caption"
        : primaryNotification
          ? "notification"
          : null;

  return (
    <div className="charlie-context-layer" role="region" aria-label="Contextual Notifications and Captions">
      {/* One dominant transient context. Approvals/errors outrank ordinary captions. */}
      {contextKind === "runtime" && (
        <div className="charlie-runtime-context" role="status" aria-live="polite" data-runtime-phase={visualRuntime.phase}>
          <span className="charlie-runtime-context-label">{visualRuntime.label}</span>
          {visualRuntime.detail && <span className="charlie-runtime-context-detail">{visualRuntime.detail}</span>}
        </div>
      )}

      {contextKind === "caption" && captionText && (
        <div className="charlie-caption-container" role="status" aria-live="polite">
          <div className="charlie-caption-box">
            <span>{captionText}</span>
          </div>
        </div>
      )}

      {/* 2. Contextual Notification Toasts */}
      {contextKind === "notification" && primaryNotification && (
        <div className="absolute top-6 right-8 flex flex-col gap-2 z-40 max-w-sm pointer-events-auto">
          <div className="p-3 rounded-lg bg-slate-950/90 border border-cyan-400/30 text-left text-xs shadow-lg backdrop-blur-md">
            <div className="flex justify-between items-center text-cyan-300 font-medium mb-1">
              <span>{primaryNotification.title || "NOTIFICATION"}</span>
              {onDismissIntent && (
                <button
                  type="button"
                  aria-label={`Dismiss ${primaryNotification.title || "notification"}`}
                  onClick={() => onDismissIntent(primaryNotification.id)}
                  className="text-slate-400 hover:text-cyan-200 cursor-pointer ml-2"
                >
                  ✕
                </button>
              )}
            </div>
            <p className="text-slate-200">{primaryNotification.summary}</p>
          </div>
        </div>
      )}

      {/* 3. High Attention / Approval Modal */}
      {contextKind === "attention" && activeAttention && (
        <div className="charlie-attention-modal-backdrop" role="alertdialog" aria-modal="true">
          <div
            ref={attentionRef}
            className="p-6 rounded-2xl bg-slate-950/95 border-2 border-amber-400/60 shadow-2xl max-w-md w-full text-center"
            aria-labelledby={`attention-title-${activeAttention.id}`}
            tabIndex={-1}
          >
            <div className="w-10 h-10 mx-auto mb-3 rounded-full bg-amber-400/20 border border-amber-400/50 flex items-center justify-center text-amber-300 font-bold">
              !
            </div>
            <h3 id={`attention-title-${activeAttention.id}`} className="text-base font-bold text-amber-200 mb-2">{activeAttention.title || "APPROVAL REQUIRED"}</h3>
            <p className="text-xs text-slate-200 mb-6 leading-relaxed">{activeAttention.summary}</p>
            <div className="flex gap-3 justify-center">
              {typeof activeAttention.content.request_id === "string" ? (
                <p className="text-[11px] text-slate-400" role="status">
                  Approval handled by Charlie&apos;s approval dialog.
                </p>
              ) : onDismissIntent ? (
                <button
                  type="button"
                  onClick={() => onDismissIntent(activeAttention.id)}
                  className="px-4 py-2 text-xs font-semibold rounded-lg bg-cyan-950/80 border border-cyan-400/40 text-cyan-200 hover:bg-cyan-900 cursor-pointer transition"
                >
                  Acknowledge
                </button>
              ) : null}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
