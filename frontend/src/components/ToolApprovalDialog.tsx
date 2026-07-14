"use client";

import type { ReactElement } from "react";
import { useCharlieStore } from "../store/useCharlieStore";

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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-md transition-opacity">
      <div className="w-full max-w-lg rounded-2xl bg-[#1c1d24]/90 border border-white/10 p-6 shadow-2xl flex flex-col gap-5 glass">
        <div>
          <h3 className="text-lg font-semibold text-white">Approval Required</h3>
          <p className="text-sm text-gray-400 mt-1">
            Charlie wants to run something that needs your confirmation.
          </p>
        </div>

        <div className="space-y-3">
          <div>
            <span className="text-xs uppercase tracking-wider font-semibold text-amber-400">Reason</span>
            <p className="mt-1 text-sm text-gray-300">{activeToolApproval.reason}</p>
          </div>

          <div>
            <span className="text-xs uppercase tracking-wider font-semibold text-blue-400">
              {activeToolApproval.tool_name}
            </span>
            <div className="mt-1 px-3 py-2 rounded-lg bg-black/20 border border-white/10 text-xs font-mono text-gray-300 break-all max-h-32 overflow-y-auto whitespace-pre-wrap">
              {argsSummary}
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-3 mt-2">
          <button
            onClick={handleRejectClick}
            className="px-4 py-2 text-sm font-medium rounded-xl border border-red-500/30 hover:border-red-500 text-red-400 hover:bg-red-500/10 transition cursor-pointer"
          >
            Decline
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
