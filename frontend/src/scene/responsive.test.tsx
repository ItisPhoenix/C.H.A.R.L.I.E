import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useCharlieStore } from "../store/charlie";
import { useWorkspaceStore } from "../layout/workspaceStore";
import { useWidgetStore } from "../layout/widgetStore";
import { INITIAL_VISUAL_RUNTIME } from "../runtime/visualRuntime";
import { CharlieScene } from "./CharlieScene";

const sceneCss = readFileSync(resolve(process.cwd(), "src/scene/scene.css"), "utf8");

function variable(name: string): string {
  const match = sceneCss.match(new RegExp(`--${name}\\s*:\\s*([^;]+);`));
  if (!match) throw new Error(`Missing scene CSS variable: ${name}`);
  return match[1].trim();
}

function clampValue(rule: string, viewport: { width: number; height: number }, axis: "width" | "height"): number {
  const match = rule.match(/^clamp\((\d+)px,\s*([\d.]+)(vw|vh),\s*(\d+)px\)$/);
  if (!match) throw new Error(`Expected a viewport clamp, got: ${rule}`);
  const preferred = Number(match[2]) * (axis === "width" ? viewport.width : viewport.height) / 100;
  return Math.min(Math.max(Number(match[1]), preferred), Number(match[4]));
}

describe("CharlieScene responsive layout authority", () => {
  const viewports = [
    { name: "Small Window", width: 800, height: 600 },
    { name: "Laptop", width: 1366, height: 768 },
    { name: "FHD Monitor", width: 1920, height: 1080 },
    { name: "Ultrawide", width: 2560, height: 1080 },
    { name: "4K UHD", width: 3840, height: 2160 },
  ];

  beforeEach(() => {
    useCharlieStore.setState({
      connected: true,
      coreState: "idle",
      visualRuntime: { ...INITIAL_VISUAL_RUNTIME, phase: "idle", label: "IDLE", detail: null },
      presentationIntents: {},
      activeCaption: null,
      activeToolApproval: null,
    });
    useWorkspaceStore.setState({ workspaces: {}, activeWorkspaceId: null, recentWorkspaces: [] });
    useWidgetStore.setState({ widgets: {}, topZIndex: 10, focusedWidgetId: null, pinnedLayouts: {} });
  });

  test("scene CSS owns viewport bounds and responsive core sizes", () => {
    expect(sceneCss).toMatch(/\.charlie-scene-root[\s\S]*width:\s*100vw;[\s\S]*height:\s*100vh;/);
    expect(variable("scene-safe-x")).toMatch(/^clamp\(/);
    expect(variable("scene-safe-y")).toMatch(/^clamp\(/);
    expect(variable("core-center-size")).toMatch(/^clamp\(/);
    expect(sceneCss).toMatch(/@media \(max-width: 1439px\)[\s\S]*--core-docked-size:\s*178px/);
    expect(sceneCss).toMatch(/@media \(max-width: 1099px\)[\s\S]*--core-docked-size:\s*156px/);
  });

  for (const viewport of viewports) {
    test(`authoritative safe margins leave a positive scene at ${viewport.name}`, () => {
      const safeX = clampValue(variable("scene-safe-x"), viewport, "width");
      const safeY = clampValue(variable("scene-safe-y"), viewport, "height");
      const coreSize = clampValue(variable("core-center-size"), viewport, "width");

      expect(viewport.width - safeX * 2).toBeGreaterThan(0);
      expect(viewport.height - safeY * 2).toBeGreaterThan(0);
      expect(coreSize).toBeGreaterThan(0);
    });

    test(`renders an accessible idle scene at ${viewport.name}`, () => {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: viewport.width });
      Object.defineProperty(window, "innerHeight", { configurable: true, value: viewport.height });
      render(
        <MemoryRouter>
          <CharlieScene />
        </MemoryRouter>,
      );

      expect(screen.getByRole("main")).toHaveAttribute("data-scene-mode", "idle");
      expect(screen.getByRole("main")).toHaveAttribute("data-core-position", "center");
      expect(screen.getByTestId("charlie-core")).toHaveClass("charlie-core-center");
    });
  }

  test("renders an active workspace with a docked core", () => {
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: {
        id: "responsive-research",
        kind: "workspace",
        title: "Research",
        summary: "Grounded findings",
        workspace_type: "research",
      },
    });
    render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>,
    );

    expect(screen.getByRole("main")).toHaveAttribute("data-scene-mode", "active");
    expect(screen.getByRole("main")).toHaveAttribute("data-core-position", "dock_bottom_right");
    expect(screen.getByTestId("charlie-core")).toHaveClass("charlie-core-docked");
    expect(screen.getByRole("region", { name: /primary workspace/i })).toBeInTheDocument();
  });
});
