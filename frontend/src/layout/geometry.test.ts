import { describe, expect, test } from "vitest";
import {
  clampToViewport,
  findCollisionFreePosition,
  fromNormalizedRect,
  rectsOverlap,
  toNormalizedRect,
  type Rect,
  type Size,
} from "./geometry";

describe("Layout geometry and collision algorithms", () => {
  test("rectsOverlap detects overlapping and non-overlapping rectangles", () => {
    const r1: Rect = { x: 100, y: 100, width: 200, height: 100 };
    const r2: Rect = { x: 150, y: 150, width: 200, height: 100 };
    const r3: Rect = { x: 400, y: 400, width: 200, height: 100 };

    expect(rectsOverlap(r1, r2)).toBe(true);
    expect(rectsOverlap(r1, r3)).toBe(false);
  });

  test("clampToViewport keeps rectangles inside safe margin box", () => {
    const vp: Size = { width: 1000, height: 800 };
    const safeMargin = { x: 20, y: 20 };

    // Off-screen left/top
    const r1: Rect = { x: -50, y: -20, width: 200, height: 150 };
    const clamped1 = clampToViewport(r1, vp, safeMargin);
    expect(clamped1.x).toBe(20);
    expect(clamped1.y).toBe(20);

    // Off-screen right/bottom
    const r2: Rect = { x: 950, y: 750, width: 200, height: 150 };
    const clamped2 = clampToViewport(r2, vp, safeMargin);
    expect(clamped2.x).toBe(780); // 1000 - 20 - 200
    expect(clamped2.y).toBe(630); // 800 - 20 - 150
  });

  test("findCollisionFreePosition moves candidate away from obstacles", () => {
    const vp: Size = { width: 1200, height: 900 };
    const obstacle: Rect = { x: 800, y: 50, width: 300, height: 200 };
    const candidate: Rect = { x: 800, y: 50, width: 300, height: 200 };

    const freePos = findCollisionFreePosition(candidate, [obstacle], vp);
    expect(rectsOverlap(freePos, obstacle)).toBe(false);
  });

  test("normalized coordinates serialize and deserialize accurately", () => {
    const vp: Size = { width: 1920, height: 1080 };
    const original: Rect = { x: 1500, y: 40, width: 320, height: 180 };

    const norm = toNormalizedRect(original, vp);
    expect(norm.anchor).toBe("top_right");
    expect(norm.offsetX).toBe(1920 - (1500 + 320)); // 100
    expect(norm.offsetY).toBe(40);

    // Restore on same viewport
    const restored = fromNormalizedRect(norm, vp);
    expect(restored.x).toBe(1500);
    expect(restored.y).toBe(40);
    expect(restored.width).toBe(320);
    expect(restored.height).toBe(180);

    // Restore on smaller viewport (e.g. 1366x768)
    const smallVp: Size = { width: 1366, height: 768 };
    const restoredSmall = fromNormalizedRect(norm, smallVp);
    expect(restoredSmall.x).toBe(1366 - 320 - 100);
    expect(restoredSmall.y).toBe(40);
  });
});
