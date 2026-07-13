"use client";

import type { ReactElement } from "react";
import { useCharlieStore } from "../store/useCharlieStore";

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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-md transition-opacity">
      <div className="w-full max-w-lg rounded-2xl bg-[#1c1d24]/90 border border-white/10 p-6 shadow-2xl flex flex-col gap-5 glass">
        <div>
          <h3 className="text-lg font-semibold text-white">Command Recovery Proposal</h3>
          <p className="text-sm text-gray-400 mt-1">
            Charlie encountered an error and generated a proposed fix. Please review and approve/reject execution.
          </p>
        </div>

        <div className="space-y-3">
          <div>
            <span className="text-xs uppercase tracking-wider font-semibold text-red-400">Original Error & Command</span>
            <div className="mt-1 px-3 py-2 rounded-lg bg-red-950/20 border border-red-500/20 text-xs font-mono text-red-300 break-all max-h-24 overflow-y-auto">
              [{activeProposal.failure_class}] {activeProposal.original_command}
            </div>
          </div>

          <div>
            <span className="text-xs uppercase tracking-wider font-semibold text-emerald-400">Proposed Command Fix</span>
            <div className="mt-1 px-3 py-2 rounded-lg bg-emerald-950/20 border border-emerald-500/20 text-xs font-mono text-emerald-300 break-all">
              {activeProposal.proposed_command}
            </div>
          </div>

          {activeProposal.explanation && (
            <div>
              <span className="text-xs uppercase tracking-wider font-semibold text-blue-400">Explanation</span>
              <p className="mt-1 text-sm text-gray-300">
                {activeProposal.explanation}
              </p>
            </div>
          )}

          <div className="flex items-center gap-2 mt-2">
            <span className={`w-2 h-2 rounded-full ${activeProposal.safeguard_passed ? 'bg-emerald-500' : 'bg-red-500'}`} />
            <span className="text-xs text-gray-400">
              {activeProposal.safeguard_passed ? "Safety Guardrails: Passed" : "Safety Guardrails: Blocked"}
            </span>
          </div>
        </div>

        <div className="flex justify-end gap-3 mt-2">
          <button
            onClick={handleRejectClick}
            className="px-4 py-2 text-sm font-medium rounded-xl border border-red-500/30 hover:border-red-500 text-red-400 hover:bg-red-500/10 transition cursor-pointer"
          >
            Reject Fix
          </button>
          <button
            onClick={handleApproveClick}
            className="px-4 py-2 text-sm font-medium rounded-xl bg-emerald-500 hover:bg-emerald-600 text-white transition cursor-pointer shadow-lg shadow-emerald-500/20"
          >
            Approve & Run
          </button>
        </div>
      </div>
    </div>
  );
}
