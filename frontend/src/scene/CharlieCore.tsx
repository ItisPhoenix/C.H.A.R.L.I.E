import { useState, type ReactElement } from "react";
import { Ring } from "../dashboard/Ring";
import type { CorePosition } from "./sceneState";

interface CharlieCoreProps {
  position: CorePosition;
  coreState: string;
  onClearScreen?: () => void;
  onOpenRecent?: () => void;
  onOpenLegacyDashboard?: () => void;
}

export function CharlieCore({
  position,
  coreState,
  onClearScreen,
  onOpenRecent,
  onOpenLegacyDashboard,
}: CharlieCoreProps): ReactElement {
  const [showMenu, setShowMenu] = useState(false);

  const isDocked = position === "dock_bottom_right";

  return (
    <div
      className={`charlie-core-wrapper ${isDocked ? "charlie-core-docked" : "charlie-core-center"}`}
      style={{
        transition: "all 420ms cubic-bezier(0.16, 1, 0.3, 1)",
      }}
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
        <Ring />

        {/* Compact core context menu */}
        {showMenu && (
          <div
            className="absolute -top-40 left-1/2 transform -translate-x-1/2 p-2 rounded-xl bg-slate-950/95 border border-cyan-400/40 shadow-2xl backdrop-blur-lg flex flex-col gap-1 z-50 min-w-[150px]"
            onClick={(e) => e.stopPropagation()}
          >
            {onOpenRecent && (
              <button
                type="button"
                onClick={() => {
                  onOpenRecent();
                  setShowMenu(false);
                }}
                className="px-3 py-1.5 text-xs text-left text-cyan-200 hover:bg-cyan-900/50 rounded transition cursor-pointer"
              >
                Recent Workspaces
              </button>
            )}
            {onClearScreen && (
              <button
                type="button"
                onClick={() => {
                  onClearScreen();
                  setShowMenu(false);
                }}
                className="px-3 py-1.5 text-xs text-left text-cyan-200 hover:bg-cyan-900/50 rounded transition cursor-pointer"
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
                className="px-3 py-1.5 text-xs text-left text-cyan-200 hover:bg-cyan-900/50 rounded transition cursor-pointer"
              >
                Legacy Dashboard
              </button>
            )}
            <button
              type="button"
              onClick={() => setShowMenu(false)}
              className="px-3 py-1 text-[10px] text-slate-400 hover:text-slate-200 text-center mt-1 border-t border-cyan-500/20"
            >
              Close Menu
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
