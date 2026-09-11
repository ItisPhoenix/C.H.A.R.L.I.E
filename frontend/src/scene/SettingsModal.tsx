import type { ReactElement } from "react";
import { useModalFocus } from "../components/useModalFocus";
import { Settings } from "./settings/Settings";

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function SettingsModal({ isOpen, onClose }: SettingsModalProps): ReactElement | null {
  const dialogRef = useModalFocus<HTMLDivElement>(isOpen, onClose);
  if (!isOpen) return null;

  return (
    <div
      className="charlie-settings-overlay fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        className="charlie-settings-modal charlie-settings-shell w-full bg-slate-950/90 border border-cyan-400/35 flex flex-col overflow-hidden font-mono pointer-events-auto"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="charlie-settings-title"
        tabIndex={-1}
      >
        {/* Modal Header */}
        <div className="charlie-settings-header flex items-center justify-between px-6 py-4 border-b border-cyan-500/20 bg-slate-900/60">
          <div className="flex items-center gap-3">
            <div className="charlie-settings-signal w-2.5 h-2.5 rounded-full bg-cyan-400 animate-pulse" />
            <div className="charlie-settings-title-block">
              <div className="charlie-settings-kicker">CHARLIE / CONTROL SURFACE</div>
              <h2 id="charlie-settings-title" className="text-sm font-bold text-cyan-200 uppercase tracking-wider">
                CHARLIE CONFIGURATION &amp; SYSTEM SETTINGS
              </h2>
            </div>
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
        <div className="charlie-settings-modal-body flex-1 p-6 overflow-y-auto font-sans text-left">
          <Settings />
        </div>
      </div>
    </div>
  );
}
