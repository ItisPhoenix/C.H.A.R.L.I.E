import type { ReactElement } from "react";
import { useLayoutStore } from "./layoutStore";

const DOCK_ITEMS = [
  ["chat", "Chat"],
  ["tasks", "Tasks"],
  ["system", "System"],
  ["tools", "Tools"],
  ["terminal", "Terminal"],
  ["mcp", "MCP"],
  ["media", "Media"],
  ["calendar", "Calendar"],
  ["settings", "Settings"],
] as const;

export function PanelDock(): ReactElement {
  const panels = useLayoutStore((state) => state.panels);
  const open = useLayoutStore((state) => state.open);
  const close = useLayoutStore((state) => state.close);
  const resetAll = useLayoutStore((state) => state.resetAll);

  return (
    <nav className="hud-dock" aria-label="Dashboard widgets">
      {DOCK_ITEMS.map(([id, label]) => (
        <button
          key={id}
          type="button"
          className={panels[id]?.open ? "is-active" : ""}
          aria-pressed={Boolean(panels[id]?.open)}
          aria-label={`${panels[id]?.open ? "Hide" : "Show"} ${label} widget`}
          title={`${panels[id]?.open ? "Hide" : "Show"} ${label}`}
          onClick={() => (panels[id]?.open ? close(id) : open(id))}
        >
          <span>{label}</span>
        </button>
      ))}
      <button type="button" aria-label="Reset dashboard widgets" title="Reset dashboard widgets" onClick={resetAll}>
        <span>Reset</span>
      </button>
    </nav>
  );
}
