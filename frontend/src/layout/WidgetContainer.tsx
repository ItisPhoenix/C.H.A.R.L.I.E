import { useRef, useState, type PointerEvent, type ReactElement } from "react";
import type { WidgetInstance } from "./widgetStore";
import { SurfaceComposer } from "../composer/SurfaceComposer";
import { SystemWidget } from "../scene/widgets/SystemWidget";

interface WidgetContainerProps {
  widget: WidgetInstance;
  onFocus: (id: string) => void;
  onDrag: (id: string, newPos: { x: number; y: number }) => void;
  onResize: (id: string, newSize: { width: number; height: number }) => void;
  onPin: (id: string) => void;
  onUnpin: (id: string) => void;
  onPauseExpiry: (id: string) => void;
  onResumeExpiry: (id: string) => void;
  onDismiss: (id: string) => void;
}

export function WidgetContainer({
  widget,
  onFocus,
  onDrag,
  onResize,
  onPin,
  onUnpin,
  onPauseExpiry,
  onResumeExpiry,
  onDismiss,
}: WidgetContainerProps): ReactElement | null {
  const isSystemWidget = widget.widgetType === "system_metric" || widget.widgetType === "system";
  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const dragStartRef = useRef<{ mouseX: number; mouseY: number; widgetX: number; widgetY: number }>({
    mouseX: 0,
    mouseY: 0,
    widgetX: 0,
    widgetY: 0,
  });
  const resizeStartRef = useRef<{ mouseX: number; mouseY: number; startW: number; startH: number }>({
    mouseX: 0,
    mouseY: 0,
    startW: 0,
    startH: 0,
  });

  if (widget.minimized) return null;

  // Handle Dragging
  const handlePointerDownHeader = (e: PointerEvent<HTMLDivElement>) => {
    // Only drag on primary button
    if (e.button !== 0) return;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    setIsDragging(true);
    onFocus(widget.id);
    onPauseExpiry(widget.id);

    dragStartRef.current = {
      mouseX: e.clientX,
      mouseY: e.clientY,
      widgetX: widget.position.x,
      widgetY: widget.position.y,
    };
  };

  const handlePointerMoveHeader = (e: PointerEvent<HTMLDivElement>) => {
    if (!isDragging) return;
    const dx = e.clientX - dragStartRef.current.mouseX;
    const dy = e.clientY - dragStartRef.current.mouseY;
    onDrag(widget.id, {
      x: dragStartRef.current.widgetX + dx,
      y: dragStartRef.current.widgetY + dy,
    });
  };

  const handlePointerUpHeader = (e: PointerEvent<HTMLDivElement>) => {
    if (!isDragging) return;
    try {
      (e.target as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      // Ignore pointer release errors
    }
    setIsDragging(false);
    onResumeExpiry(widget.id);
  };

  // Handle Resizing
  const handlePointerDownResize = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    setIsResizing(true);
    onFocus(widget.id);
    onPauseExpiry(widget.id);

    resizeStartRef.current = {
      mouseX: e.clientX,
      mouseY: e.clientY,
      startW: widget.size.width,
      startH: widget.size.height,
    };
  };

  const handlePointerMoveResize = (e: PointerEvent<HTMLDivElement>) => {
    if (!isResizing) return;
    const dx = e.clientX - resizeStartRef.current.mouseX;
    const dy = e.clientY - resizeStartRef.current.mouseY;
    onResize(widget.id, {
      width: resizeStartRef.current.startW + dx,
      height: resizeStartRef.current.startH + dy,
    });
  };

  const handlePointerUpResize = (e: PointerEvent<HTMLDivElement>) => {
    if (!isResizing) return;
    try {
      (e.target as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      // Ignore
    }
    setIsResizing(false);
    onResumeExpiry(widget.id);
  };

  return (
    <div
      className={`charlie-widget-card group absolute border transition-shadow select-none ${
        widget.focused
          ? "border-cyan-400/50 bg-slate-950/90 shadow-cyan-500/10"
          : "border-cyan-500/20 bg-slate-950/80 hover:border-cyan-500/35"
      }`}
      data-widget-type={widget.widgetType}
      style={{
        transform: `translate3d(${widget.position.x}px, ${widget.position.y}px, 0)`,
        width: `${widget.size.width}px`,
        height: `${widget.size.height}px`,
        zIndex: widget.zIndex,
        touchAction: "none",
      }}
      onMouseEnter={() => onPauseExpiry(widget.id)}
      onMouseLeave={() => {
        if (!isDragging && !isResizing) onResumeExpiry(widget.id);
      }}
      onClick={() => onFocus(widget.id)}
      role="region"
      aria-label={`${widget.title} widget`}
    >
      {/* Corner Bracket Accents */}
      <span className="absolute -top-[1px] -left-[1px] w-2.5 h-2.5 border-t border-l border-cyan-400/70 pointer-events-none" />
      <span className="absolute -top-[1px] -right-[1px] w-2.5 h-2.5 border-t border-r border-cyan-400/70 pointer-events-none" />
      <span className="absolute -bottom-[1px] -left-[1px] w-2.5 h-2.5 border-b border-l border-cyan-400/70 pointer-events-none" />
      <span className="absolute -bottom-[1px] -right-[1px] w-2.5 h-2.5 border-b border-r border-cyan-400/70 pointer-events-none" />
      {/* Header bar (Drag Handle) */}
      {!isSystemWidget && <div
        className="flex items-center justify-between px-3 py-1 cursor-grab active:cursor-grabbing border-b border-cyan-500/15"
        onPointerDown={handlePointerDownHeader}
        onPointerMove={handlePointerMoveHeader}
        onPointerUp={handlePointerUpHeader}
      >
        <div className="flex items-center gap-2 overflow-hidden">
          <span className="text-[10px] font-mono text-cyan-400 uppercase tracking-wider truncate">
            {widget.widgetType}
          </span>
          {widget.pinned && (
            <span className="text-[10px] text-cyan-300 font-mono" title="Pinned to spatial canvas">
              [PINNED]
            </span>
          )}
        </div>

        {/* Action Controls (Subtle, revealed on hover/focus) */}
        <div className="flex items-center gap-1.5 opacity-60 group-hover:opacity-100 transition-opacity">
          {/* Pin Button */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              if (widget.pinned) onUnpin(widget.id);
              else onPin(widget.id);
            }}
            className={`px-1.5 py-0.5 text-[10px] rounded border transition cursor-pointer ${
              widget.pinned
                ? "bg-cyan-950/80 border-cyan-400/60 text-cyan-300"
                : "border-cyan-500/20 text-slate-400 hover:text-cyan-200"
            }`}
            title={widget.pinned ? "Unpin widget" : "Pin widget to canvas"}
          >
            {widget.pinned ? "★" : "☆"}
          </button>

          {/* Close Button */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onDismiss(widget.id);
            }}
            className="px-1.5 py-0.5 text-[10px] rounded text-slate-400 hover:text-cyan-200 hover:bg-cyan-950/40 cursor-pointer"
            title="Dismiss widget [Esc]"
          >
            ✕
          </button>
        </div>
      </div>}

      {/* Body content */}
      <div className={isSystemWidget ? "p-4 overflow-auto text-left h-full" : "p-3.5 overflow-auto text-left h-[calc(100%-42px)]"}>
        {isSystemWidget ? (
          <SystemWidget widget={widget} />
        ) : widget.content?.surface_spec || widget.content?.schema_version || widget.widgetType === "composed_surface" ? (
          <SurfaceComposer
            spec={
              (widget.content.surface_spec as Record<string, unknown>) ||
              (widget.content.schema_version ? widget.content : {
                schema_version: 1,
                surface_id: widget.id,
                title: widget.title,
                target: "widget",
                revision: 1,
                primitives: [{ type: "text", data: { text: widget.summary } }],
              })
            }
          />
        ) : (
          <>
            <h4 className="text-xs font-semibold text-slate-200 mb-1">{widget.title}</h4>
            <p className="text-xs text-cyan-100/85 leading-relaxed">{widget.summary}</p>
          </>
        )}
      </div>

      {/* Resize Handle (Bottom-Right Corner) */}
      <div
        className="absolute bottom-0 right-0 w-3.5 h-3.5 cursor-nwse-resize opacity-0 group-hover:opacity-60 hover:!opacity-100 flex items-center justify-center text-[10px] text-cyan-400"
        onPointerDown={handlePointerDownResize}
        onPointerMove={handlePointerMoveResize}
        onPointerUp={handlePointerUpResize}
        title="Resize widget"
      >
        ⌟
      </div>
    </div>
  );
}
