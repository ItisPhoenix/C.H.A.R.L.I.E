import { describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { SpatialMapPrimitive } from "./SpatialMapPrimitive";
import { DensityHeatmapPrimitive } from "./DensityHeatmapPrimitive";
import { TelemetryGaugesPrimitive } from "./TelemetryGaugesPrimitive";
import { ProcessTelemetryPrimitive } from "./ProcessTelemetryPrimitive";

describe("Phase 9 Spatial Primitives Suite", () => {
  test("SpatialMapPrimitive renders radar mode with distance rings and layer pills", () => {
    render(
      <SpatialMapPrimitive
        data={{
          mode: "radar",
          title: "RADAR FEED",
          nodes: [{ id: "n1", label: "VESSEL_A", x: 50, y: 50 }],
          layers: [{ id: "l1", label: "VESSEL", color: "#22d3ee" }],
        }}
      />
    );
    expect(screen.getByText("RADAR FEED")).toBeDefined();
    expect(screen.getByText("REAL-TIME SPATIAL FEED")).toBeDefined();
    expect(screen.getByText("VESSEL")).toBeDefined();
    expect(screen.getByText("VESSEL_A")).toBeDefined();
  });

  test("SpatialMapPrimitive renders geo mode with world hubs", () => {
    render(
      <SpatialMapPrimitive
        data={{
          mode: "geo",
          title: "GLOBAL MAP",
          nodes: [{ id: "g1", label: "TEST_HUB", x: 40, y: 40 }],
          layers: [{ id: "l1", label: "HUBS", color: "#22d3ee" }],
        }}
      />
    );
    expect(screen.getByText("GLOBAL MAP")).toBeDefined();
    expect(screen.getByText("HUBS")).toBeDefined();
    expect(screen.getByText("TEST_HUB")).toBeDefined();
  });

  test("SpatialMapPrimitive renders topology mode with nodes and links", () => {
    render(
      <SpatialMapPrimitive
        data={{
          mode: "topology",
          title: "NETWORK TOPOLOGY",
          nodes: [{ id: "t1", label: "NODE_ALPHA", x: 30, y: 30 }],
          layers: [{ id: "l1", label: "NODES", color: "#22d3ee" }],
        }}
      />
    );
    expect(screen.getByText("NETWORK TOPOLOGY")).toBeDefined();
    expect(screen.getByText("NODES")).toBeDefined();
    expect(screen.getByText("NODE_ALPHA")).toBeDefined();
  });

  test("DensityHeatmapPrimitive renders intensity grid with gradient bar", () => {
    render(
      <DensityHeatmapPrimitive
        data={{
          title: "ACTIVITY DENSITY",
          points: [{ x: 5, y: 5, value: 0.8 }],
        }}
      />
    );
    expect(screen.getByText("ACTIVITY DENSITY")).toBeDefined();
    expect(screen.getByText("LOW")).toBeDefined();
    expect(screen.getByText("HIGH")).toBeDefined();
  });

  test("TelemetryGaugesPrimitive renders circular radial gauges and vitals", () => {
    render(
      <TelemetryGaugesPrimitive
        data={{
          title: "SYSTEM STATUS",
          gauges: [
            { id: "cpu", label: "CPU", value: 32 },
            { id: "mem", label: "MEMORY", value: 61 },
          ],
          stats: [{ label: "SYSTEM TEMP", value: "42°C" }],
        }}
      />
    );
    expect(screen.getByText("SYSTEM STATUS")).toBeDefined();
    expect(screen.getByText("CPU")).toBeDefined();
    expect(screen.getByText("MEMORY")).toBeDefined();
    expect(screen.getByText("SYSTEM TEMP")).toBeDefined();
  });

  test("ProcessTelemetryPrimitive renders live processes table with status badges", () => {
    render(
      <ProcessTelemetryPrimitive
        data={{
          title: "WHAT IS RUNNING",
          processes: [{ name: "test.worker", pid: 1234, status: "RUNNING", uptime: "1h" }],
        }}
      />
    );
    expect(screen.getByText("WHAT IS RUNNING")).toBeDefined();
    expect(screen.getByText("PROCESS")).toBeDefined();
    expect(screen.getByText("PID")).toBeDefined();
    expect(screen.getByText("STATUS")).toBeDefined();
    expect(screen.getByText("test.worker")).toBeDefined();
  });
});
