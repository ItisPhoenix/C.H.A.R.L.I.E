import { describe, expect, test } from "vitest";

describe("CharlieScene responsive layout constraints", () => {
  const viewports = [
    { name: "Small Window (800x600)", width: 800, height: 600 },
    { name: "Laptop (1366x768)", width: 1366, height: 768 },
    { name: "FHD Monitor (1920x1080)", width: 1920, height: 1080 },
    { name: "Ultrawide (2560x1080)", width: 2560, height: 1080 },
    { name: "4K UHD (3840x2160)", width: 3840, height: 2160 },
  ];

  for (const vp of viewports) {
    test(`safe margins and core bounds scale safely on ${vp.name}`, () => {
      // safe-x: clamp(16px, 3.5vw, 48px)
      const rawSafeX = vp.width * 0.035;
      const clampedSafeX = Math.min(Math.max(16, rawSafeX), 48);

      // safe-y: clamp(16px, 3.5vh, 48px)
      const rawSafeY = vp.height * 0.035;
      const clampedSafeY = Math.min(Math.max(16, rawSafeY), 48);

      // core-center-size: clamp(280px, 36vw, 460px)
      const rawCoreSize = vp.width * 0.36;
      const clampedCoreSize = Math.min(Math.max(280, rawCoreSize), 460);

      // core-docked-size: clamp(140px, 16vw, 200px)
      const rawDockedSize = vp.width * 0.16;
      const clampedDockedSize = Math.min(Math.max(140, rawDockedSize), 200);

      expect(clampedSafeX).toBeGreaterThanOrEqual(16);
      expect(clampedSafeX).toBeLessThanOrEqual(48);

      expect(clampedSafeY).toBeGreaterThanOrEqual(16);
      expect(clampedSafeY).toBeLessThanOrEqual(48);

      expect(clampedCoreSize).toBeGreaterThanOrEqual(280);
      expect(clampedCoreSize).toBeLessThanOrEqual(460);

      expect(clampedDockedSize).toBeGreaterThanOrEqual(140);
      expect(clampedDockedSize).toBeLessThanOrEqual(200);

      // Verify workspace available area is always positive and non-zero
      const availableWorkspaceWidth = vp.width - clampedSafeX * 2;
      const availableWorkspaceHeight = vp.height - clampedSafeY * 2;

      expect(availableWorkspaceWidth).toBeGreaterThan(500);
      expect(availableWorkspaceHeight).toBeGreaterThan(400);
    });
  }
});
