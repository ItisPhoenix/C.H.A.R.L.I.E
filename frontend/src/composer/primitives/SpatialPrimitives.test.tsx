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
    expect(screen.getByText("SPATIAL FEED")).toBeDefined();
    expect(screen.getByText("VESSEL")).toBeDefined();
    expect(screen.getByText("VESSEL_A")).toBeDefined();
  });

  test("SpatialMapPrimitive renders geo mode with world hubs", () => {
    render(
      <SpatialMapPrimitive
        data={{
          mode: "geo",
          useRealEngine: false,
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
          title: "TRAFFIC DENSITY",
          subtitle: "HOURLY SPREAD",
          points: [{ x: 2, y: 3, value: 0.9 }],
        }}
      />
    );
    expect(screen.getByText("TRAFFIC DENSITY")).toBeDefined();
    expect(screen.getByText("HOURLY SPREAD")).toBeDefined();
    expect(screen.getByText("LOW")).toBeDefined();
    expect(screen.getByText("HIGH")).toBeDefined();
  });

  test("TelemetryGaugesPrimitive renders radial bars with current values", () => {
    render(
      <TelemetryGaugesPrimitive
        data={{
          title: "CORE VITALS",
          gauges: [
            { id: "g1", label: "CPU_LOAD", value: 45, unit: "%" },
            { id: "g2", label: "RAM_USAGE", value: 68, unit: "%" },
          ],
        }}
      />
    );
    expect(screen.getByText("CORE VITALS")).toBeDefined();
    expect(screen.getByText("CPU_LOAD")).toBeDefined();
    expect(screen.getByText("RAM_USAGE")).toBeDefined();
  });

  test("ProcessTelemetryPrimitive renders process activity list with status chips", () => {
    render(
      <ProcessTelemetryPrimitive
        data={{
          title: "RUNTIME PROCESSES",
          processes: [
            { name: "charlie_core", pid: 1042, cpu: 12, memory: 140, status: "running" },
            { name: "voice_pipeline", pid: 1043, cpu: 5, memory: 80, status: "idle" },
          ],
        }}
      />
    );
    expect(screen.getByText("RUNTIME PROCESSES")).toBeDefined();
    expect(screen.getByText("charlie_core")).toBeDefined();
    expect(screen.getByText("voice_pipeline")).toBeDefined();
  });
});
