import { describe, expect, test, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SurfaceComposer } from "./SurfaceComposer";
import type { SurfaceSpec } from "./surfaceSchema";

describe("SurfaceComposer schema validation and primitives", () => {
  const validComparisonSpec: SurfaceSpec = {
    schema_version: 1,
    surface_id: "gpu-compare-1",
    title: "GPU Hardware Comparison",
    target: "workspace",
    revision: 1,
    surface_type: "comparison",
    summary: "Comparing top GPUs",
    layout: { type: "stack", gap: 12 },
    primitives: [
      { type: "heading", id: "h1", data: { text: "GPU Benchmark Breakdown", level: 2 } },
      { type: "metric", id: "m1", data: { label: "RTX 4090 VRAM", value: "24", unit: "GB", status: "success" } },
      {
        type: "table",
        id: "t1",
        data: {
          columns: [
            { key: "model", label: "Model" },
            { key: "vram", label: "VRAM" },
            { key: "fps", label: "4K FPS", align: "right" },
          ],
          rows: [
            { model: "RTX 4090", vram: "24GB", fps: "120" },
            { model: "RX 7900 XTX", vram: "24GB", fps: "105" },
          ],
        },
      },
      {
        type: "chart",
        id: "c1",
        data: {
          chartType: "bar",
          title: "Relative 4K Performance",
          data: [
            { label: "RTX 4090", value: 100 },
            { label: "RX 7900 XTX", value: 88 },
          ],
        },
      },
      {
        type: "timeline",
        id: "time1",
        data: {
          items: [
            { time: "10:00", title: "Benchmark started", status: "completed" },
            { time: "10:05", title: "4K Raytracing Test", status: "active", summary: "Evaluating ray bounds" },
          ],
        },
      },
      {
        type: "source",
        id: "src1",
        data: {
          title: "Tom's Hardware GPU Hierarchy",
          domain: "tomshardware.com",
          url: "https://tomshardware.com/gpus",
          snippet: "The RTX 4090 remains the fastest consumer graphics card.",
          confidence: 0.95,
        },
      },
      {
        type: "status",
        id: "st1",
        data: { status: "success", text: "VERIFIED" },
      },
    ],
    actions: [
      { id: "act-sort", label: "Sort by Performance", action_id: "sort_perf", variant: "primary" },
    ],
  };

  test("renders valid surface spec with all primitives and actions", () => {
    const handleAction = vi.fn();
    render(<SurfaceComposer spec={validComparisonSpec} onAction={handleAction} />);

    expect(screen.getByText("GPU Benchmark Breakdown")).toBeDefined();
    expect(screen.getByText("RTX 4090 VRAM")).toBeDefined();
    expect(screen.getByText("24")).toBeDefined();
    expect(screen.getByText("Tom's Hardware GPU Hierarchy")).toBeDefined();
    expect(screen.getByText("VERIFIED")).toBeDefined();

    const actionBtn = screen.getByRole("button", { name: "Sort by Performance" });
    expect(actionBtn).toBeDefined();

    fireEvent.click(actionBtn);
    expect(handleAction).toHaveBeenCalledWith(
      expect.objectContaining({ action_id: "sort_perf" })
    );
  });

  test("rejects invalid schema version with clean error notice", () => {
    const invalidSpec = {
      schema_version: 99,
      surface_id: "invalid-1",
      title: "Old Surface",
      primitives: [],
    };

    render(<SurfaceComposer spec={invalidSpec} />);
    expect(screen.getByText(/Surface Schema Validation Error/i)).toBeDefined();
    expect(screen.getByText(/Unsupported schema_version/i)).toBeDefined();
  });

  test("rejects dangerous script or javascript URL injection", () => {
    const dangerousSpec = {
      schema_version: 1,
      surface_id: "xss-1",
      title: "<script>alert(1)</script>",
      primitives: [
        { type: "text", data: { text: "Hello <script>bad()</script>" } },
        { type: "image", data: { src: "javascript:alert(1)" } },
      ],
    };

    render(<SurfaceComposer spec={dangerousSpec} />);
    expect(screen.getByText(/Surface Schema Validation Error/i)).toBeDefined();
    expect(screen.getAllByText(/Dangerous script\/HTML pattern/i).length).toBeGreaterThan(0);
  });

  test("live patching: updates in place with higher revision", () => {
    const { rerender } = render(<SurfaceComposer spec={validComparisonSpec} />);
    expect(screen.getByText("GPU Benchmark Breakdown")).toBeDefined();

    const updatedSpec: SurfaceSpec = {
      ...validComparisonSpec,
      revision: 2,
      primitives: [
        ...validComparisonSpec.primitives,
        {
          type: "metric",
          id: "m2",
          data: { label: "RTX 4090 TBP", value: "450", unit: "W", status: "warning" },
        },
      ],
    };

    rerender(<SurfaceComposer spec={updatedSpec} />);
    expect(screen.getByText("RTX 4090 TBP")).toBeDefined();
    expect(screen.getByText("450")).toBeDefined();
  });
});
