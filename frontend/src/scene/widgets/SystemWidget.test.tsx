import { describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { SystemWidget } from "./SystemWidget";
import type { WidgetInstance } from "../../layout/widgetStore";

describe("SystemWidget Component", () => {
  const dummyWidget: WidgetInstance = {
    id: "widget-sys-1",
    presentationIntentId: "intent-sys-1",
    taskId: null,
    title: "CPU USAGE",
    summary: "Current CPU is at 18%",
    widgetType: "system_metric",
    position: { x: 100, y: 100 },
    size: { width: 280, height: 160 },
    zIndex: 10,
    focused: false,
    pinned: false,
    minimized: false,
    replaceKey: null,
    autoDismissMs: 5000,
    expiresAt: Date.now() + 5000,
    pausedExpiry: false,
    zone: "contextual",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    content: {
      metric_name: "CPU USAGE",
      value: 18,
      temperature: 43,
      fan_speed: 916,
      history: [12, 14, 13, 16, 18],
    },
  };

  test("renders metric value, sparkline, core temperature, and fan speed", () => {
    render(<SystemWidget widget={dummyWidget} />);

    expect(screen.getByText("SYSTEM")).toBeDefined();
    expect(screen.getByText("CPU USAGE")).toBeDefined();
    expect(screen.getByText("18%")).toBeDefined();
    expect(screen.getByText("43°C")).toBeDefined();
    expect(screen.getByText("916 RPM")).toBeDefined();
  });
});
