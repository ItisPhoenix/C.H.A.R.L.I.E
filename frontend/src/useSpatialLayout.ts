import { useLayoutEffect, useRef, type RefObject } from "react";
import { compose, type Layout, type SpatialItem } from "./spatial";

export interface SpatialMetadata {
  id: string;
  importance: number;
  interactionPriority?: number;
  relatedTo?: string;
  core?: boolean;
  immersive?: boolean;
  preferredZone?: "center" | "around-core" | "bottom-right";
  widgetId?: string;
  widgetLifecycle?: string;
  preferredFootprint?: "compact" | "medium" | "large";
  draggable?: boolean;
  pinnedPosition?: { x: number; y: number };
}

export interface SpatialResetRequest {
  id: string;
  token: number;
}

/** Observe intrinsic boxes; transforms never feed back into their dimensions. */
export function useSpatialLayout(
  canvas: RefObject<HTMLDivElement | null>,
  items: SpatialMetadata[],
  refreshKey: string | number = "",
  draggingId: string | null = null,
  resetRequest: SpatialResetRequest | null = null,
) {
  const previous = useRef<Layout>(undefined);
  const animations = useRef(new Map<string, Animation>());
  const consumedReset = useRef<number | null>(null);

  useLayoutEffect(() => {
    const root = canvas.current;
    if (!root) return;
    const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
    const stopMotion = () => {
      if (reducedMotion.matches) {
        animations.current.forEach(animation => animation.cancel());
        animations.current.clear();
      }
    };
    reducedMotion.addEventListener("change", stopMotion);
    let frame = 0;
    const measure = () => {
      const style = getComputedStyle(root);
      const gap = parseFloat(style.getPropertyValue("--spatial-gap"));
      const available = { width: root.clientWidth, height: root.parentElement!.clientHeight };
      const elements = new Map(
        [...root.querySelectorAll<HTMLElement>(":scope > [data-spatial-id]")]
          .map(element => [element.dataset.spatialId!, element]),
      );
      const measured: SpatialItem[] = items.flatMap(item => {
        const element = elements.get(item.id);
        return element && element.offsetWidth && element.offsetHeight
          ? [{ ...item, width: element.offsetWidth, height: element.offsetHeight }] : [];
      });
      if (!available.width || !available.height || measured.length !== items.length) return;
      let continuity = previous.current;
      if (resetRequest && consumedReset.current !== resetRequest.token) {
        consumedReset.current = resetRequest.token;
        continuity = previous.current ? {
          ...previous.current,
          positions: Object.fromEntries(Object.entries(previous.current.positions)
            .filter(([id]) => id !== resetRequest.id)),
        } : undefined;
      }
      const next = compose(available, measured, continuity, { gap });
      for (const [id, animation] of animations.current) {
        if (!next.positions[id]) { animation.cancel(); animations.current.delete(id); }
      }
      for (const item of measured) {
        const element = elements.get(item.id)!;
        if (item.id === draggingId) {
          element.dataset.measuredWidth = String(item.width);
          element.dataset.measuredHeight = String(item.height);
          element.dataset.layoutScale = String(previous.current?.positions[item.id]?.scale ?? 1);
          element.dataset.measured = "true";
          element.dataset.placed = "true";
          continue;
        }
        const rect = next.positions[item.id];
        const old = continuity?.positions[item.id];
        const scale = rect.scale ?? 1;
        const transform = `translate(${rect.x}px, ${rect.y}px) scale(${scale})`;
        element.dataset.measuredWidth = String(item.width);
        element.dataset.measuredHeight = String(item.height);
        element.dataset.layoutScale = String(scale);
        element.dataset.measured = "true";
        element.dataset.x = String(rect.x);
        element.dataset.y = String(rect.y);
        const currentTransform = getComputedStyle(element).transform;
        const moved = !old || old.x !== rect.x || old.y !== rect.y;
        if (moved) {
          animations.current.get(item.id)?.cancel();
          element.style.transform = transform;
          element.style.transformOrigin = "top left";
          // Fade relocation prevents a straight movement path crossing another projection.
          if (old && !item.pinnedPosition && !reducedMotion.matches && element.animate) {
            const animation = element.animate([
              { transform: currentTransform, opacity: 1, offset: 0 },
              { transform: currentTransform, opacity: 0, offset: 0.4 },
              { transform, opacity: 0, offset: 0.6 },
              { transform, opacity: 1, offset: 1 },
            ], { duration: 260, easing: "ease-out" });
            animations.current.set(item.id, animation);
            animation.onfinish = () => {
              if (animations.current.get(item.id) === animation) animations.current.delete(item.id);
            };
          }
        }
        element.dataset.placed = "true";
      }
      root.style.height = `${next.height}px`;
      root.dataset.layoutHeight = String(next.height);
      root.dataset.measured = "true";
      previous.current = next;
    };
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(root.parentElement!);
    root.querySelectorAll(":scope > [data-spatial-id]").forEach(element => observer.observe(element));
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      animations.current.forEach(animation => animation.cancel());
      animations.current.clear();
      reducedMotion.removeEventListener("change", stopMotion);
    };
  }, [canvas, draggingId, items, refreshKey, resetRequest]);
}
