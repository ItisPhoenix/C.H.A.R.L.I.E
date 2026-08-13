import { useRef, type CSSProperties, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactElement, type ReactNode } from "react";
import { ArrowClockwiseIcon, MinusIcon, XIcon } from "@phosphor-icons/react";
import { useLayoutStore } from "./layoutStore";

export function Panel({ id, title, children }: { id: string; title: string; children: ReactNode }): ReactElement | null {
  const layout = useLayoutStore((state) => state.panels[id]);
  const move = useLayoutStore((state) => state.move);
  const resize = useLayoutStore((state) => state.resize);
  const focus = useLayoutStore((state) => state.focus);
  const toggleMinimize = useLayoutStore((state) => state.toggleMinimize);
  const close = useLayoutStore((state) => state.close);
  const resetPosition = useLayoutStore((state) => state.resetPosition);
  const dragRef = useRef<{ startX: number; startY: number; originX: number; originY: number } | null>(null);
  const resizeRef = useRef<{ startX: number; startY: number; originW: number; originH: number } | null>(null);

  if (!layout?.open) return null;

  function startDrag(event: ReactPointerEvent<HTMLDivElement>): void {
    if ((event.target as HTMLElement).closest("button")) return;
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

  function startResize(event: ReactPointerEvent<HTMLButtonElement>): void {
    event.stopPropagation();
    focus(id);
    resizeRef.current = { startX: event.clientX, startY: event.clientY, originW: layout.w, originH: layout.h };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function resizePanel(event: ReactPointerEvent<HTMLButtonElement>): void {
    if (!resizeRef.current) return;
    resize(id, resizeRef.current.originW + event.clientX - resizeRef.current.startX, resizeRef.current.originH + event.clientY - resizeRef.current.startY);
  }

  function handleHeaderKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    if (event.target !== event.currentTarget) return;
    const step = 10;
    if (event.key === "ArrowRight") move(id, layout.x + step, layout.y);
    else if (event.key === "ArrowLeft") move(id, layout.x - step, layout.y);
    else if (event.key === "ArrowDown") move(id, layout.x, layout.y + step);
    else if (event.key === "ArrowUp") move(id, layout.x, layout.y - step);
    else return;
    event.preventDefault();
  }

  return (
    <section
      className={`hud-panel hud-panel-${id}${layout.minimized ? " is-minimized" : ""}`}
      style={{
        "--panel-x": `${layout.x}px`,
        "--panel-y": `${layout.y}px`,
        "--panel-width": `${layout.w}px`,
        "--panel-height": `${layout.minimized ? 42 : layout.h}px`,
        zIndex: layout.z,
      } as CSSProperties}
      onPointerDown={() => focus(id)}
    >
      <header className="hud-panel-header" tabIndex={0} aria-label="Panel header" onKeyDown={handleHeaderKeyDown} onDoubleClick={() => resetPosition(id)} onPointerDown={startDrag} onPointerMove={drag} onPointerUp={() => { dragRef.current = null; }}>
        <span className="hud-ring-dot" aria-hidden="true" />
        <h2>{title}</h2>
        <div className="hud-panel-actions">
          <button type="button" aria-label={layout.minimized ? "Restore" : "Minimize"} title={layout.minimized ? "Restore panel" : "Minimize panel"} onPointerDown={(event) => event.stopPropagation()} onClick={() => toggleMinimize(id)}><MinusIcon aria-hidden="true" weight="bold" /></button>
          <button type="button" aria-label="Reset position" title="Reset panel position" onPointerDown={(event) => event.stopPropagation()} onClick={() => resetPosition(id)}><ArrowClockwiseIcon aria-hidden="true" weight="bold" /></button>
          <button type="button" aria-label="Close" title="Close panel" onPointerDown={(event) => event.stopPropagation()} onClick={() => close(id)}><XIcon aria-hidden="true" weight="bold" /></button>
        </div>
      </header>
      {!layout.minimized && <div className="hud-panel-body">{children}</div>}
      {!layout.minimized && <button type="button" className="hud-panel-resize" aria-label="Resize panel" onPointerDown={startResize} onPointerMove={resizePanel} onPointerUp={() => { resizeRef.current = null; }} />}
    </section>
  );
}
