import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import type { SpatialMetadata } from "../useSpatialLayout";

export interface ProjectionData extends SpatialMetadata {
  treatment: "free" | "structured" | "hard" | "integrated";
  heading?: string;
  label?: string;
  content: ReactNode;
  depth: "background" | "active" | "priority";
  draggable?: boolean;
}

export interface ProjectionInteractionProps {
  onDragStart?: (id: string, event: ReactPointerEvent<HTMLSpanElement>) => void;
  onDragMove?: (id: string, event: ReactPointerEvent<HTMLSpanElement>) => void;
  onDragEnd?: (id: string, event: ReactPointerEvent<HTMLSpanElement>) => void;
  onDragCancel?: (id: string, event: ReactPointerEvent<HTMLSpanElement>) => void;
  onResetDrag?: (id: string) => void;
}

export function Projection({ item, leaving = false, onDragStart, onDragMove, onDragEnd, onDragCancel, onResetDrag }: {
  item: ProjectionData; leaving?: boolean;
} & ProjectionInteractionProps) {
  return (
    <article className="spatial-item projection" data-spatial-id={item.id}
      data-projection-id={item.id} data-treatment={item.treatment}
      data-depth={item.depth} data-widget-id={item.widgetId} data-widget-lifecycle={item.widgetLifecycle}
      data-widget-footprint={item.preferredFootprint} data-widget-immersive={item.immersive || undefined}
      data-pinned={item.pinnedPosition ? "true" : undefined}
      data-leaving={leaving || undefined} inert={leaving}
      aria-label={item.heading ?? item.label ?? item.id}>
      {item.draggable && <span className="projection-drag-handle" data-drag-region title="Drag to reposition"
        aria-hidden="true"
        onPointerDown={event => onDragStart?.(item.id, event)}
        onPointerMove={event => onDragMove?.(item.id, event)}
        onPointerUp={event => onDragEnd?.(item.id, event)}
        onPointerCancel={event => onDragCancel?.(item.id, event)} />}
      {item.draggable && item.pinnedPosition && onResetDrag && <button type="button" data-no-drag
        className="projection-reset" title="Reset widget position" aria-label="Reset widget position"
        onClick={() => onResetDrag(item.id)}>↺</button>}
      {(item.treatment === "structured" || item.treatment === "hard") &&
        <span className="projection-tab" aria-hidden="true" />}
      <div className="projection-body">
        {item.label && <p className="eyebrow">{item.label}</p>}
        {item.heading && <h2>{item.heading}</h2>}
        {item.content}
      </div>
    </article>
  );
}
