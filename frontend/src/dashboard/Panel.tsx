import { useRef, type PointerEvent as ReactPointerEvent, type ReactElement, type ReactNode } from "react";
import { useLayoutStore } from "./layoutStore";

export function Panel({ id, title, children }: { id: string; title: string; children: ReactNode }): ReactElement | null {
  const layout = useLayoutStore((state) => state.panels[id]);
  const move = useLayoutStore((state) => state.move);
  const focus = useLayoutStore((state) => state.focus);
  const toggleMinimize = useLayoutStore((state) => state.toggleMinimize);
  const close = useLayoutStore((state) => state.close);
  const resetPosition = useLayoutStore((state) => state.resetPosition);
  const dragRef = useRef<{ startX: number; startY: number; originX: number; originY: number } | null>(null);

  if (!layout?.open) return null;

  function startDrag(event: ReactPointerEvent<HTMLDivElement>): void {
    focus(id);
    dragRef.current = { startX: event.clientX, startY: event.clientY, originX: layout.x, originY: layout.y };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function drag(event: ReactPointerEvent<HTMLDivElement>): void {
    if (!dragRef.current) return;
    const stage = event.currentTarget.closest(".hud-stage") as HTMLElement | null;
    const scale = Number.parseFloat(stage?.style.getPropertyValue("--hud-scale") ?? "1") || 1;
    move(
      id,
      dragRef.current.originX + (event.clientX - dragRef.current.startX) / scale,
      dragRef.current.originY + (event.clientY - dragRef.current.startY) / scale,
    );
  }

  return (
    <section
      className={`hud-panel hud-panel-${id}${layout.minimized ? " is-minimized" : ""}`}
      style={{ left: layout.x, top: layout.y, width: layout.w, height: layout.minimized ? 42 : layout.h, zIndex: layout.z }}
      onPointerDown={() => focus(id)}
    >
      <header className="hud-panel-header" onDoubleClick={() => resetPosition(id)} onPointerDown={startDrag} onPointerMove={drag} onPointerUp={() => { dragRef.current = null; }}>
        <span className="hud-ring-dot" aria-hidden="true" />
        <h2>{title}</h2>
        <div className="hud-panel-actions">
          <button type="button" aria-label={layout.minimized ? "Restore" : "Minimize"} onClick={() => toggleMinimize(id)}>−</button>
          <button type="button" aria-label="Reset position" onClick={() => resetPosition(id)}>⌁</button>
          <button type="button" aria-label="Close" onClick={() => close(id)}>×</button>
        </div>
      </header>
      {!layout.minimized && <div className="hud-panel-body">{children}</div>}
    </section>
  );
}
