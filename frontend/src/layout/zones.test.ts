import { describe, expect, test } from "vitest";
import { rectsOverlap } from "./geometry";
import { getZoneCandidateRect, type ZoneContext } from "./zones";

describe("measured spatial zones", () => {
  test("bottom-right placement follows the measured core, not a fixed core size", () => {
    const context: ZoneContext = {
      viewport: { width: 1366, height: 768 },
      safeMargin: { x: 24, y: 24 },
      coreBounds: { x: 1120, y: 560, width: 178, height: 178 },
      workspaceBounds: null,
    };
    const candidate = getZoneCandidateRect("bottom_right", { width: 280, height: 130 }, context);

    expect(candidate.x).toBe(824);
    expect(candidate.y).toBe(414);
    expect(rectsOverlap(candidate, context.coreBounds)).toBe(false);
  });

  test("a wider docked core changes placement from the same viewport", () => {
    const base: ZoneContext = {
      viewport: { width: 1920, height: 1080 },
      safeMargin: { x: 48, y: 48 },
      coreBounds: { x: 1520, y: 800, width: 200, height: 200 },
      workspaceBounds: null,
    };
    const wider: ZoneContext = {
      ...base,
      coreBounds: { x: 1320, y: 720, width: 460, height: 300 },
    };

    const baseCandidate = getZoneCandidateRect("bottom_right", undefined, base);
    const widerCandidate = getZoneCandidateRect("bottom_right", undefined, wider);
    expect(widerCandidate.x).toBeLessThan(baseCandidate.x);
    expect(widerCandidate.y).toBeLessThan(baseCandidate.y);
  });
});
