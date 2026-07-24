"use client";

import type { ReactElement } from "react";
import { useCharlieStore } from "../store/useCharlieStore";
import { Modal, ModalField, ModalActions } from "./Modal";

interface ToolApprovalDialogProps {
  onApprove: (requestId: string) => void;
  onReject: (requestId: string) => void;
}

export function ToolApprovalDialog({ onApprove, onReject }: ToolApprovalDialogProps): ReactElement | null {
  const activeToolApproval = useCharlieStore((s) => s.activeToolApproval);
  const setActiveToolApproval = useCharlieStore((s) => s.setActiveToolApproval);

  if (!activeToolApproval) return null;

  const handleApproveClick = () => {
    onApprove(activeToolApproval.request_id);
    setActiveToolApproval(null);
  };

  const handleRejectClick = () => {
    onReject(activeToolApproval.request_id);
    setActiveToolApproval(null);
  };

  const argsSummary = Object.entries(activeToolApproval.arguments || {})
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join("\n");

  return (
    <Modal labelledBy="tool-approval-title">
      <div>
        <h3 id="tool-approval-title" className="text-lg font-semibold text-[var(--color-text-primary)]">
          Approval Required
        </h3>
        <p className="text-sm text-[var(--color-text-muted)] mt-1">
          Charlie wants to run something that needs your confirmation.
        </p>
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

      <ModalActions
        rejectLabel="Decline"
        approveLabel="Approve & Run"
        onReject={handleRejectClick}
        onApprove={handleApproveClick}
      />
    </Modal>
  );
}
