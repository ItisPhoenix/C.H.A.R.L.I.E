import { useState, type ReactElement } from "react";
import { Ring } from "../dashboard/Ring";
import type { CorePosition } from "./sceneState";

interface CharlieCoreProps {
  position: CorePosition;
  coreState: string;
  activeWorkspaceType?: string | null;
  customStatusLabel?: string;
  customSubtext?: string;
  onClearScreen?: () => void;
  onOpenRecent?: () => void;
  onOpenSettings?: () => void;
  onOpenLegacyDashboard?: () => void;
}

export function CharlieCore({
  position,
  coreState,
  customStatusLabel,
  customSubtext,
  onClearScreen,
  onOpenRecent,
  onOpenSettings,
  onOpenLegacyDashboard,
}: CharlieCoreProps): ReactElement {
  const [showMenu, setShowMenu] = useState(false);

  const isDocked = position === "dock_bottom_right";

  // Centered mode status metadata
  const stateLabel = customStatusLabel || coreState.toUpperCase();
  const subtext =
    customSubtext ||
    (coreState === "idle"
      ? "I'M HERE WHEN YOU NEED ME."
      : coreState === "listening"
        ? "AWAITING INPUT"
        : coreState === "speaking"
          ? ""
          : "TASK IN PROGRESS");

  return (
    <div
      className={`charlie-core-wrapper ${isDocked ? "charlie-core-docked" : "charlie-core-center"}`}
      onClick={() => setShowMenu((prev) => !prev)}
      role="button"
      tabIndex={0}
      aria-label={`Charlie core in ${coreState} state. Click for menu.`}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          setShowMenu((prev) => !prev);
        }
      }}
    >
      <div className="w-full h-full relative flex items-center justify-center">
        {/* Core Ring */}
        <Ring />

        {/* Center Inner Branding */}
        <div className="charlie-core-brand-center" aria-hidden="true">
          C.H.A.R.L.I.E.
        </div>

        {/* Below-Core Status Bar: Rendered ONLY in Centered Idle mode (Docked mode shows CORE ONLY) */}
        {!isDocked && (
          <div className="charlie-core-status-bar" aria-hidden="true">
            <div className="charlie-core-state-label">{stateLabel}</div>
            <div className="charlie-core-state-subtext">{subtext}</div>
            <div className="charlie-core-indicator-dots">
              <span className="dot dot-active" />
              <span className="dot dot-inactive" />
              <span className="dot dot-inactive" />
            </div>
          </div>
        )}

        {/* Compact core context menu */}
        {showMenu && (
          <div
            className="absolute -top-48 left-1/2 transform -translate-x-1/2 p-2 rounded-xl bg-slate-950/95 border border-cyan-400/40 shadow-2xl backdrop-blur-lg flex flex-col gap-1 z-50 min-w-[160px] pointer-events-auto"
            onClick={(e) => e.stopPropagation()}
          >
            {onOpenRecent && (
              <button
                type="button"
                onClick={() => {
                  onOpenRecent();
                  setShowMenu(false);
                }}
                className="px-3 py-1.5 text-xs text-left text-cyan-200 hover:bg-cyan-900/50 rounded transition cursor-pointer font-mono"
              >
                Recent Workspaces
              </button>
            )}
            {onOpenSettings && (
              <button
                type="button"
                onClick={() => {
                  onOpenSettings();
                  setShowMenu(false);
                }}
                className="px-3 py-1.5 text-xs text-left text-cyan-200 hover:bg-cyan-900/50 rounded transition cursor-pointer font-mono"
              >
                Settings
              </button>
            )}
            {onClearScreen && (
              <button
                type="button"
                onClick={() => {
                  onClearScreen();
                  setShowMenu(false);
                }}
                className="px-3 py-1.5 text-xs text-left text-cyan-200 hover:bg-cyan-900/50 rounded transition cursor-pointer font-mono"
              >
                Clear Screen
              </button>
            )}
            {onOpenLegacyDashboard && (
              <button
                type="button"
                onClick={() => {
                  onOpenLegacyDashboard();
                  setShowMenu(false);
                }}
                className="px-3 py-1.5 text-xs text-left text-cyan-200 hover:bg-cyan-900/50 rounded transition cursor-pointer font-mono"
              >
                Legacy Dashboard
              </button>
            )}
            <button
              type="button"
              onClick={() => setShowMenu(false)}
              className="px-3 py-1 text-[10px] text-slate-400 hover:text-slate-200 text-center mt-1 border-t border-cyan-500/20 font-mono"
            >
              Close Menu
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
