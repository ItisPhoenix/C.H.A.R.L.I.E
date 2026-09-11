import { useEffect, type ReactElement } from "react";
import type { AlertInfo, PresentationIntent } from "../store/charlie";
import type { VisualRuntimeState } from "../runtime/visualRuntime";
import { sendCommand } from "../runtime/bridge";
import { useModalFocus } from "../components/useModalFocus";

interface ContextLayerProps {
  captionText: string | null;
  notifications: PresentationIntent[];
  activeAttention: PresentationIntent | null;
  activeAlert: AlertInfo | null;
  visualRuntime: VisualRuntimeState;
  activeSessionId: string | null;
  connected: boolean;
  onDismissIntent?: (id: string) => void;
  onDismissAlert?: () => void;
  onClearVisualRuntime?: (expectedUpdatedAt?: string) => void;
}

export function ContextLayer({
  captionText,
  notifications,
  activeAttention,
  activeAlert,
  visualRuntime,
  activeSessionId,
  connected,
  onDismissIntent,
  onDismissAlert,
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
  const alertDominant = Boolean(activeAlert) && ["idle", "success", "degraded"].includes(visualRuntime.phase);
  const contextKind = activeAttention
    ? "attention"
    : alertDominant
      ? "alert"
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
        <div
          className="charlie-runtime-context"
          role="status"
          aria-live={visualRuntime.phase === "error" ? "assertive" : "polite"}
          data-runtime-phase={visualRuntime.phase}
          data-recovery-proposal-id={visualRuntime.recoveryProposalId ?? undefined}
        >
          <span className="charlie-runtime-context-label">{visualRuntime.label}</span>
          {visualRuntime.detail && <span className="charlie-runtime-context-detail">{visualRuntime.detail}</span>}
          {visualRuntime.phase === "recovering" &&
            visualRuntime.recoveryProposalId &&
            visualRuntime.correlation.sessionId &&
            connected &&
            activeSessionId === visualRuntime.correlation.sessionId && (
            <span className="charlie-runtime-context-actions">
              <button
                type="button"
                className="charlie-runtime-context-action charlie-runtime-context-action--approve"
                onClick={() => sendCommand("recovery_approve", {
                  proposal_id: visualRuntime.recoveryProposalId,
                  session_id: visualRuntime.correlation.sessionId,
                })}
              >
                Approve recovery
              </button>
              <button
                type="button"
                className="charlie-runtime-context-action"
                onClick={() => sendCommand("recovery_reject", {
                  proposal_id: visualRuntime.recoveryProposalId,
                  session_id: visualRuntime.correlation.sessionId,
                })}
              >
                Reject
              </button>
            </span>
          )}
        </div>
      )}

      {contextKind === "alert" && activeAlert && (
        <div className="charlie-runtime-context" role="alert" aria-live="assertive" data-runtime-phase="alert">
          <span className="charlie-runtime-context-label">{activeAlert.severity.toUpperCase()}</span>
          <span className="charlie-runtime-context-detail">{activeAlert.message}</span>
          {onDismissAlert && (
            <button type="button" aria-label="Dismiss alert" className="charlie-runtime-context-action" onClick={onDismissAlert}>✕</button>
          )}
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
