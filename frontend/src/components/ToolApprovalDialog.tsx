import type { ReactElement } from "react";
import { useCharlieStore } from "../store/charlie";
import { sendCommand } from "../runtime/bridge";
import { Modal, ModalField, ModalActions } from "./Modal";

// Ported from frontend@c7aa7df~1's ToolApprovalDialog.tsx, rewired onto the new store/bridge.
export function ToolApprovalDialog(): ReactElement | null {
  const activeToolApproval = useCharlieStore((s) => s.activeToolApproval);
  const setActiveToolApproval = useCharlieStore((s) => s.setActiveToolApproval);

  if (!activeToolApproval) return null;

  const respond = (approved: boolean) => {
    sendCommand(approved ? "tool_approve" : "tool_reject", { request_id: activeToolApproval.request_id });
    setActiveToolApproval(null);
  };

  const argsSummary = Object.entries(activeToolApproval.arguments || {})
    .map(([key, value]) => `${key}: ${typeof value === "object" && value !== null ? JSON.stringify(value) : String(value)}`)
    .join("\n");
  const accent = activeToolApproval.risk_class === "destructive" ? "danger" : "warning";

  return (
    <Modal labelledBy="tool-approval-title" accent={accent}>
      <div>
        <h3 id="tool-approval-title" className="flex items-center gap-2 text-lg font-semibold text-[var(--color-text-primary)]">
          <span className={`role-dot role-dot-${accent} w-2 h-2 rounded-full shrink-0`} aria-hidden="true" />
          Approval Required
        </h3>
        <p className="text-sm text-[var(--color-text-muted)] mt-1">Charlie wants to run something that needs your confirmation.</p>
      </div>

      <div className="space-y-3">
        <ModalField label="Reason" tone="warning">
          {activeToolApproval.reason}
        </ModalField>
        <ModalField label={activeToolApproval.tool_name}>
          <div className="px-3 py-2 rounded-lg bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] text-xs font-mono break-all max-h-32 overflow-y-auto whitespace-pre-wrap">
            {argsSummary}
          </div>
        </ModalField>
      </div>

      <ModalActions rejectLabel="Decline" approveLabel="Approve & Run" onReject={() => respond(false)} onApprove={() => respond(true)} />
    </Modal>
  );
}
