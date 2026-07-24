"use client";

import type { ReactElement } from "react";
import { useCharlieStore } from "../store/useCharlieStore";
import { Modal, ModalField, ModalActions } from "./Modal";

interface RecoveryDialogProps {
  onApprove: (proposalId: string) => void;
  onReject: (proposalId: string) => void;
}

export function RecoveryDialog({ onApprove, onReject }: RecoveryDialogProps): ReactElement | null {
  const activeProposal = useCharlieStore((s) => s.activeProposal);
  const setActiveProposal = useCharlieStore((s) => s.setActiveProposal);

  if (!activeProposal) return null;

  const handleApproveClick = () => {
    onApprove(activeProposal.proposal_id);
    setActiveProposal(null);
  };

  const handleRejectClick = () => {
    onReject(activeProposal.proposal_id);
    setActiveProposal(null);
  };

  return (
    <Modal labelledBy="recovery-dialog-title">
      <div>
        <h3 id="recovery-dialog-title" className="text-lg font-semibold text-[var(--color-text-primary)]">
          Command Recovery Proposal
        </h3>
        <p className="text-sm text-[var(--color-text-muted)] mt-1">
          Charlie hit an error running a command and has a proposed fix. Review it before it runs.
        </p>
      </div>

      <div className="space-y-3">
        <ModalField label="Original error & command" tone="error">
          <div className="px-3 py-2 rounded-lg bg-[var(--color-status-error-dim)] border border-[var(--color-status-error)]/20 text-xs font-mono break-all max-h-24 overflow-y-auto">
            [{activeProposal.failure_class}] {activeProposal.original_command}
          </div>
        </ModalField>

        <ModalField label="Proposed fix" tone="success">
          <div className="px-3 py-2 rounded-lg bg-[var(--color-status-success-dim)] border border-[var(--color-status-success)]/20 text-xs font-mono break-all">
            {activeProposal.proposed_command}
          </div>
        </ModalField>

        {activeProposal.explanation && (
          <ModalField label="Explanation">{activeProposal.explanation}</ModalField>
        )}

        <div className="flex items-center gap-2 mt-2">
          <span
            className="w-2 h-2 rounded-full shrink-0"
            style={{
              background: activeProposal.safeguard_passed
                ? "var(--color-status-success)"
                : "var(--color-status-error)",
            }}
            aria-hidden="true"
          />
          <span className="text-xs text-[var(--color-text-muted)]">
            {activeProposal.safeguard_passed ? "Safety guardrails: passed" : "Safety guardrails: blocked"}
          </span>
        </div>
      </div>

      <ModalActions
        rejectLabel="Reject Fix"
        approveLabel="Approve & Run"
        onReject={handleRejectClick}
        onApprove={handleApproveClick}
      />
    </Modal>
  );
}
