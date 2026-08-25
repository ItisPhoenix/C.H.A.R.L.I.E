import type { ReactElement } from "react";
import { useWorkspaceStore } from "./workspaceStore";
import { sendCommand } from "../runtime/bridge";

interface RecentWorkspacesModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function RecentWorkspacesModal({ isOpen, onClose }: RecentWorkspacesModalProps): ReactElement | null {
  const recent = useWorkspaceStore((s) => s.recentWorkspaces);
  const restoreWorkspace = useWorkspaceStore((s) => s.restoreWorkspace);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 bg-black/80 backdrop-blur-md flex items-center justify-center z-50 p-4 select-none"
      role="dialog"
      aria-label="Recent Workspaces"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md p-5 rounded-2xl bg-slate-950/95 border border-cyan-500/30 shadow-2xl text-left"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-cyan-500/20 pb-3 mb-4">
          <h3 className="text-sm font-bold text-cyan-200 tracking-wider">RECENT WORKSPACES</h3>
          <button
            type="button"
            onClick={onClose}
            className="text-xs text-slate-400 hover:text-cyan-200 cursor-pointer"
          >
            ✕
          </button>
        </div>

        {recent.length === 0 ? (
          <div className="py-8 text-center text-xs text-slate-500 font-mono">
            No recent workspaces in history.
          </div>
        ) : (
          <div className="flex flex-col gap-2 max-h-80 overflow-y-auto pr-1">
            {recent.map((entry) => (
              <div
                key={entry.id}
                className="p-3 rounded-xl bg-cyan-950/20 border border-cyan-500/15 hover:border-cyan-500/40 hover:bg-cyan-950/40 transition cursor-pointer flex items-center justify-between gap-3"
                onClick={() => {
                  if (entry.taskId) {
                    sendCommand("presentation_command", { action: "focus_task", task_id: entry.taskId });
                  } else {
                    restoreWorkspace(entry.id);
                  }
                  onClose();
                }}
              >
                <div className="overflow-hidden">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[10px] font-mono text-cyan-400 uppercase">
                      {entry.type}
                    </span>
                    <span className="text-xs font-semibold text-slate-200 truncate">
                      {entry.title}
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-400 line-clamp-1">{entry.summary}</p>
                </div>
                <button
                  type="button"
                  className="px-2.5 py-1 text-[11px] font-semibold rounded bg-cyan-900/40 text-cyan-300 hover:bg-cyan-800/60 border border-cyan-500/20 shrink-0"
                >
                  Restore
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
