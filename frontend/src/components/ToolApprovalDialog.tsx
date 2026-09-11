import { useEffect, useState, type ReactElement } from "react";
import { useCharlieStore } from "../store/charlie";
import { sendCommand } from "../runtime/bridge";
import { useModalFocus } from "./useModalFocus";
import { ModalField, ModalActions } from "./Modal";
import "./ToolApprovalDialog.css";

// Ported from frontend@c7aa7df~1's ToolApprovalDialog.tsx, rewired onto the new store/bridge.
export function ToolApprovalDialog(): ReactElement | null {
  const activeToolApproval = useCharlieStore((s) => s.activeToolApproval);
  const [respondingFor, setRespondingFor] = useState<string | null>(null);
  const dialogRef = useModalFocus<HTMLDivElement>(Boolean(activeToolApproval), () => {});

  useEffect(() => {
    if (activeToolApproval) {
      dialogRef.current?.focus({ preventScroll: true });
      if (dialogRef.current) dialogRef.current.scrollTop = 0;
      if (dialogRef.current?.parentElement) dialogRef.current.parentElement.scrollTop = 0;
    }
  }, [activeToolApproval, dialogRef]);

  if (!activeToolApproval) return null;

  const respond = (approved: boolean) => {
    if (respondingFor === activeToolApproval.request_id) return;
    setRespondingFor(activeToolApproval.request_id);
    sendCommand(approved ? "tool_approve" : "tool_reject", { request_id: activeToolApproval.request_id });
  };

  const accent = activeToolApproval.risk_class === "destructive" ? "danger" : "warning";
  const riskLabel = activeToolApproval.risk_class?.trim().toUpperCase() || "NOT REPORTED";
  const actionLabel = activeToolApproval.tool_name.replaceAll("_", " ").toUpperCase();

  return (
    <section
      ref={dialogRef}
      className={`charlie-approval-field charlie-approval-field--${accent}`}
      role="dialog"
      aria-modal="true"
      aria-labelledby="tool-approval-title"
      tabIndex={-1}
    >
      <div className="charlie-approval-panel">
        <div className="charlie-approval-header">
          <div>
            <div className="charlie-approval-kicker">CHARLIE / HUMAN CHECKPOINT</div>
            <h3 id="tool-approval-title" className="flex items-center gap-2 text-2xl font-semibold text-[var(--color-text-primary)]">
              <span className={`role-dot role-dot-${accent} w-2 h-2 rounded-full shrink-0`} aria-hidden="true" />
              Approval Required
            </h3>
          </div>
          <span className={`charlie-approval-risk charlie-approval-risk--${accent}`}>{riskLabel}</span>
        </div>
        <p className="charlie-approval-lede">Charlie is deliberately holding this operation until you decide.</p>
      </div>

      <div className="charlie-approval-facts">
        <ModalField label="Requested action">
          <strong className="charlie-approval-action">{actionLabel}</strong>
        </ModalField>
        <ModalField label="Risk / severity" tone={accent === "danger" ? "error" : "warning"}>
          {riskLabel}
        </ModalField>
        <ModalField label="Why approval is required" tone="warning">
          {activeToolApproval.reason}
        </ModalField>
        <ModalField label="Target / scope">
          <span className="charlie-approval-unavailable">No sanitized target or scope was supplied by the runtime.</span>
        </ModalField>
        <ModalField label="Expected effect">
          <span className="charlie-approval-unavailable">No additional impact summary was supplied by the runtime.</span>
        </ModalField>
      </div>

      <div className="charlie-approval-safety" role="note">
        Only the runtime&apos;s sanitized approval summary is shown. Hidden tool arguments and secrets are not rendered here.
      </div>

      <div className="charlie-approval-actions">
        <ModalActions
          rejectLabel="Reject"
          approveLabel="Approve"
          onReject={() => respond(false)}
          onApprove={() => respond(true)}
          busy={respondingFor === activeToolApproval.request_id}
        />
      </div>
    </section>
  );
}
