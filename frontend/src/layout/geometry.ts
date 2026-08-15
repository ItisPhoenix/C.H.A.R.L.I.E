/**
 * Charlie V1 Geometry and Spatial Layout Primitives
 */

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Point {
  x: number;
  y: number;
}

export interface Size {
  width: number;
  height: number;
}

export interface NormalizedRect {
  anchor: "top_right" | "top_left" | "bottom_left" | "bottom_right" | "center";
  offsetX: number; // offset in pixels or percentage relative to anchor
  offsetY: number;
  width: number;
  height: number;
}

/** Check if two rectangles overlap with an optional safety margin. */
export function rectsOverlap(r1: Rect, r2: Rect, margin = 8): boolean {
  return !(
    r1.x + r1.width + margin <= r2.x ||
    r2.x + r2.width + margin <= r1.x ||
    r1.y + r1.height + margin <= r2.y ||
    r2.y + r2.height + margin <= r1.y
  );
}

/** Check if a point is inside a rectangle. */
export function pointInRect(p: Point, r: Rect): boolean {
  return p.x >= r.x && p.x <= r.x + r.width && p.y >= r.y && p.y <= r.y + r.height;
}

/** Clamp a rectangle within a safe viewport box. */
export function clampToViewport(
  rect: Rect,
  viewport: Size,
  safeMargin: { x: number; y: number } = { x: 24, y: 24 }
): Rect {
  const minX = safeMargin.x;
  const minY = safeMargin.y;
  const maxX = Math.max(minX, viewport.width - safeMargin.x - rect.width);
  const maxY = Math.max(minY, viewport.height - safeMargin.y - rect.height);

  return {
    x: Math.min(Math.max(rect.x, minX), maxX),
    y: Math.min(Math.max(rect.y, minY), maxY),
    width: Math.min(rect.width, viewport.width - safeMargin.x * 2),
    height: Math.min(rect.height, viewport.height - safeMargin.y * 2),
  };
}

/**
 * Find a collision-free position for a candidate rectangle among obstacles.
 * Tries the initial preferred position, then searches adjacent vertical and horizontal slots.
 */
export function findCollisionFreePosition(
  candidate: Rect,
  obstacles: Rect[],
  viewport: Size,
  safeMargin = { x: 24, y: 24 },
  step = 16
): Rect {
  const clamped = clampToViewport(candidate, viewport, safeMargin);

  // Check if candidate is already free of collisions
  const hasCollision = (r: Rect) => obstacles.some((obs) => rectsOverlap(r, obs));

  if (!hasCollision(clamped)) {
    return clamped;
  }

  // Search downwards first (standard card stacking), then upwards, then left/right
  const maxDeltaY = Math.max(viewport.height - clamped.y, clamped.y);
  for (let offset = step; offset <= maxDeltaY; offset += step) {
    // 1. Try below
    const below: Rect = { ...clamped, y: clamped.y + offset };
    const clampedBelow = clampToViewport(below, viewport, safeMargin);
    if (!hasCollision(clampedBelow)) {
      return clampedBelow;
    }

    // 2. Try above
    const above: Rect = { ...clamped, y: clamped.y - offset };
    const clampedAbove = clampToViewport(above, viewport, safeMargin);
    if (!hasCollision(clampedAbove)) {
      return clampedAbove;
    }
  }

  // Search horizontally if vertical is congested
  const maxDeltaX = Math.max(viewport.width - clamped.x, clamped.x);
  for (let offset = step; offset <= maxDeltaX; offset += step) {
    const toLeft: Rect = { ...clamped, x: clamped.x - offset };
    const clampedLeft = clampToViewport(toLeft, viewport, safeMargin);
    if (!hasCollision(clampedLeft)) {
      return clampedLeft;
    }
  }

  // Fallback to initial clamped position
  return clamped;
}

/** Convert a pixel rect into anchor-relative normalized coordinates for persistent storage. */
export function toNormalizedRect(rect: Rect, viewport: Size): NormalizedRect {
  const isRight = rect.x + rect.width / 2 > viewport.width / 2;
  const isBottom = rect.y + rect.height / 2 > viewport.height / 2;

  let anchor: NormalizedRect["anchor"] = "top_right";
  let offsetX = 0;
  let offsetY = 0;

  if (isRight && !isBottom) {
    anchor = "top_right";
    offsetX = viewport.width - (rect.x + rect.width);
    offsetY = rect.y;
  } else if (!isRight && !isBottom) {
    anchor = "top_left";
    offsetX = rect.x;
    offsetY = rect.y;
  } else if (isRight && isBottom) {
    anchor = "bottom_right";
    offsetX = viewport.width - (rect.x + rect.width);
    offsetY = viewport.height - (rect.y + rect.height);
  } else {
    anchor = "bottom_left";
    offsetX = rect.x;
    offsetY = viewport.height - (rect.y + rect.height);
  }

  return {
    anchor,
    offsetX: Math.round(offsetX),
    offsetY: Math.round(offsetY),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
  };
}

/** Restore pixel rect from normalized coordinates and current viewport dimensions. */
export function fromNormalizedRect(norm: NormalizedRect, viewport: Size): Rect {
  let x = norm.offsetX;
  let y = norm.offsetY;

  switch (norm.anchor) {
    case "top_right":
      x = viewport.width - norm.width - norm.offsetX;
      y = norm.offsetY;
      break;
    case "bottom_right":
      x = viewport.width - norm.width - norm.offsetX;
      y = viewport.height - norm.height - norm.offsetY;
      break;
    case "bottom_left":
      x = norm.offsetX;
      y = viewport.height - norm.height - norm.offsetY;
      break;
    case "top_left":
    default:
      x = norm.offsetX;
      y = norm.offsetY;
      break;
  }

  return clampToViewport({ x, y, width: norm.width, height: norm.height }, viewport);
}
