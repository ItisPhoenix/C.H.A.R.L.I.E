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
      unit: "percent_0_100",
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

  test("renders structured runtime metric without parsing summary text", () => {
    render(
      <SystemWidget
        widget={{ ...dummyWidget, summary: "stale summary 4%", content: { metrics: { metric_name: "Memory Utilization", value: 74.3, unit: "percent_0_100" } } }}
      />
    );

    expect(screen.getByText("Memory Utilization")).toBeDefined();
    expect(screen.getByText("74.3%")).toBeDefined();
    expect(screen.queryByText("4%")).toBeNull();
  });

  test("converts normalized fractions only under explicit fraction contract", () => {
    render(
      <SystemWidget
        widget={{ ...dummyWidget, content: { metrics: { metric_name: "Memory Utilization", value: 0.743, unit: "fraction_0_1" } } }}
      />
    );

    expect(screen.getByText("74.3%")).toBeDefined();
  });

  test("formats non-percent units explicitly and leaves unknown units unitless", () => {
    const { rerender } = render(
      <SystemWidget
        widget={{ ...dummyWidget, content: { metrics: { metric_name: "CPU Temperature", value: 67.4, unit: "celsius" } } }}
      />
    );
    expect(screen.getByText("67.4°C")).toBeDefined();

    rerender(
      <SystemWidget
        widget={{ ...dummyWidget, content: { metrics: { metric_name: "Buffer", value: 2048, unit: "bytes" } } }}
      />
    );
    expect(screen.getByText("2 KiB")).toBeDefined();

    rerender(
      <SystemWidget
        widget={{ ...dummyWidget, content: { metrics: { metric_name: "Queue", value: 7, unit: "count" } } }}
      />
    );
    expect(screen.getByText("7")).toBeDefined();
    expect(screen.queryByText("7%")).toBeNull();
  });

  test("keeps unavailable metrics explicit", () => {
    render(
      <SystemWidget
        widget={{ ...dummyWidget, content: { metrics: { metric_name: "CPU Temperature", value: null, unit: "celsius", available: false } } }}
      />
    );

    expect(screen.getByText("—")).toBeDefined();
  });

  test("does not render a sparkline without real history", () => {
    const { container } = render(
      <SystemWidget
        widget={{ ...dummyWidget, content: { metrics: { metric_name: "CPU Utilization", value: 20.5, unit: "percent_0_100" } } }}
      />
    );

    expect(container.querySelector("svg")).toBeNull();
  });

  test("keeps authoritative zero visible", () => {
    render(
      <SystemWidget
        widget={{ ...dummyWidget, content: { metrics: { metric_name: "CPU Utilization", value: 0, unit: "percent_0_100" } } }}
      />
    );

    expect(screen.getByText("0%")).toBeDefined();
  });
});
