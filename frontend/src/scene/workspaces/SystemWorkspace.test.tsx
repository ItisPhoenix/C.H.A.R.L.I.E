import { describe, expect, test, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { useCharlieStore } from "../../store/charlie";
import { SystemWorkspace } from "./SystemWorkspace";

const workspace = { contentState: {} } as WorkspaceInstance;

beforeEach(() => {
  useCharlieStore.setState({
    systemStatus: null,
    systemStatusUpdatedAt: null,
    subsystemHealth: {},
    subsystemHealthUpdatedAt: null,
    runtimeTruth: null,
    tasks: {},
  });
});

describe("SystemWorkspace live telemetry", () => {
  test("renders authoritative metrics, freshness, and degraded health", () => {
    useCharlieStore.setState({
      systemStatus: { cpu: 12, ram: null, gpu: null, disk: 64, netKbps: 3.5, uptimeSeconds: null, batteryPercent: null },
      systemStatusUpdatedAt: "2020-01-01T00:00:00.000Z",
      runtimeTruth: {
        authority: "main_runtime",
        launch_id: "launch-1",
        revision: 7,
        observed_at: "2020-01-01T00:00:00.000Z",
        status: "degraded",
        subsystems: { brain: { status: "degraded", detail: "Unavailable" } },
      },
    });

    render(<SystemWorkspace workspace={workspace} />);

    expect(screen.getByText("12%")).toBeInTheDocument();
    expect(screen.getByText("64%")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByTestId("system-telemetry-freshness")).toHaveTextContent("STALE");
    expect(screen.getByText("RUNTIME DEGRADED")).toBeInTheDocument();
    expect(screen.getByText("BRAIN: DEGRADED")).toBeInTheDocument();
    expect(screen.getByText("RUNTIME SUBSYSTEMS")).toBeInTheDocument();
  });

  test("does not invent a telemetry snapshot", () => {
    render(<SystemWorkspace workspace={workspace} />);
    expect(screen.getByTestId("system-telemetry-freshness")).toHaveTextContent("UNAVAILABLE");
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.getByText("RUNTIME UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getAllByText("RUNTIME TRUTH UNAVAILABLE").length).toBeGreaterThan(0);
  });

  test("projects canonical task activity and does not render supplied topology content", () => {
    useCharlieStore.setState({
      tasks: {
        active: {
          id: "active",
          title: "Canonical active task",
          status: "running",
          currentStep: 1,
          totalSteps: 2,
          origin: "foreground",
          progress: 0.5,
          updatedAt: "2026-09-14T10:02:00.000Z",
        },
        completed: {
          id: "completed",
          title: "Canonical completed task",
          status: "completed",
          currentStep: 1,
          totalSteps: 1,
          origin: "background",
          completedAt: "2026-09-14T10:01:00.000Z",
        },
      },
    });

    render(<SystemWorkspace workspace={{
      ...workspace,
      contentState: {
        topology: { nodes: [{ id: "fabricated-node" }], links: [{ source: "a", target: "b" }] },
        logs: [{ timestamp: "10:00:00", level: "INFO", message: "fabricated log" }],
      },
    }} />);

    expect(screen.getByText("ACTIVE TASKS")).toBeInTheDocument();
    expect(screen.getAllByText("Canonical active task").length).toBeGreaterThan(0);
    expect(screen.getByText("TASK ACTIVITY")).toBeInTheDocument();
    expect(screen.getByText("Canonical completed task")).toBeInTheDocument();
    expect(screen.getByText("CANONICAL INVENTORY // EDGE MODEL UNAVAILABLE")).toBeInTheDocument();
    expect(screen.queryByText("fabricated-node")).toBeNull();
    expect(screen.queryByText("fabricated log")).toBeNull();
  });
});
