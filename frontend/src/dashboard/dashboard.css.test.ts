/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";

const dashboardCss = readFileSync(resolve(process.cwd(), "src/dashboard/dashboard.css"), "utf8");

describe("dashboard motion preferences", () => {
  test("disables HUD transitions when reduced motion is requested", () => {
    const reducedMotionRule = dashboardCss.match(/@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*$/)?.[0] ?? "";

    expect(reducedMotionRule).toContain("transition: none");
  });
});
