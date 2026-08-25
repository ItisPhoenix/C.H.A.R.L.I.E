import type { ReactElement } from "react";
import { Settings } from "./settings/Settings";

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function SettingsModal({ isOpen, onClose }: SettingsModalProps): ReactElement | null {
  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/80 backdrop-blur-md flex items-center justify-center p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="CHARLIE System Settings"
    >
      <div
        className="w-full max-w-4xl min-h-[520px] max-h-[85vh] bg-slate-950/95 border border-cyan-400/50 rounded-2xl shadow-2xl shadow-cyan-500/10 flex flex-col overflow-hidden font-mono pointer-events-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-cyan-500/20 bg-slate-900/60">
          <div className="flex items-center gap-3">
            <div className="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-pulse" />
            <h2 className="text-sm font-bold text-cyan-200 uppercase tracking-wider">
              CHARLIE CONFIGURATION & SYSTEM SETTINGS
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="px-2.5 py-1 text-xs rounded border border-cyan-500/30 text-slate-400 hover:text-cyan-200 hover:border-cyan-400 transition cursor-pointer"
            title="Close settings [Esc]"
          >
            ✕ CLOSE
          </button>
        </div>

        {/* Modal Body hosting existing Settings system */}
        <div className="flex-1 p-6 overflow-y-auto font-sans text-left">
          <Settings />
        </div>
      </div>
    </div>
  );
}
