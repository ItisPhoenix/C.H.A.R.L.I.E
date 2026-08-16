import type { Rect, Size } from "./geometry";

export type LayoutZone =
  | "top_right"
  | "top_left"
  | "bottom_left"
  | "bottom_right"
  | "contextual"
  | "workspace_edge"
  | "center";

export interface ZoneContext {
  viewport: Size;
  safeMargin: { x: number; y: number };
  coreBounds: Rect;
  workspaceBounds: Rect | null;
}

export const DEFAULT_WIDGET_SIZE: Size = {
  width: 280,
  height: 130,
};

export const MIN_WIDGET_SIZE: Size = {
  width: 220,
  height: 100,
};

export const MAX_WIDGET_SIZE: Size = {
  width: 480,
  height: 380,
};

/**
 * Get initial candidate rect for a widget within a given zone.
 */
export function getZoneCandidateRect(
  zone: LayoutZone,
  size: Size = DEFAULT_WIDGET_SIZE,
  ctx: ZoneContext
): Rect {
  const { viewport, safeMargin, workspaceBounds } = ctx;
  const width = Math.min(size.width, viewport.width - safeMargin.x * 2);
  const height = Math.min(size.height, viewport.height - safeMargin.y * 2);

  switch (zone) {
    case "top_right":
      return {
        x: viewport.width - safeMargin.x - width,
        y: safeMargin.y,
        width,
        height,
      };

    case "top_left":
      return {
        x: safeMargin.x,
        y: safeMargin.y,
        width,
        height,
      };

    case "bottom_left":
      return {
        x: safeMargin.x,
        y: viewport.height - safeMargin.y - height,
        width,
        height,
      };

    case "bottom_right":
      // When docked core is at bottom-right, position slightly above/left of core
      return {
        x: viewport.width - safeMargin.x - width,
        y: Math.max(safeMargin.y, viewport.height - safeMargin.y - height - 220),
        width,
        height,
      };

    case "workspace_edge":
      if (workspaceBounds) {
        // Place just outside the workspace's right edge if space allows
        const targetX = workspaceBounds.x + workspaceBounds.width + 16;
        if (targetX + width <= viewport.width - safeMargin.x) {
          return {
            x: targetX,
            y: workspaceBounds.y,
            width,
            height,
          };
        }
      }
      // Fall back to top_right
      return {
        x: viewport.width - safeMargin.x - width,
        y: safeMargin.y,
        width,
        height,
      };

    case "center":
      return {
        x: (viewport.width - width) / 2,
        y: (viewport.height - height) / 2,
        width,
        height,
      };

    case "contextual":
    default:
      // Default contextual placement: top-right zone
      return {
        x: viewport.width - safeMargin.x - width,
        y: safeMargin.y,
        width,
        height,
      };
  }
}
