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
  });
});

describe("SystemWorkspace live telemetry", () => {
  test("renders authoritative metrics, freshness, and degraded health", () => {
    useCharlieStore.setState({
      systemStatus: { cpu: 12, ram: null, gpu: null, disk: 64, netKbps: 3.5, uptimeSeconds: null, batteryPercent: null },
      systemStatusUpdatedAt: "2020-01-01T00:00:00.000Z",
      subsystemHealth: { brain: { status: "degraded", detail: "Unavailable" } },
      subsystemHealthUpdatedAt: "2020-01-01T00:00:00.000Z",
    });

    render(<SystemWorkspace workspace={workspace} />);

    expect(screen.getByText("12%")).toBeInTheDocument();
    expect(screen.getByText("64%")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByTestId("system-telemetry-freshness")).toHaveTextContent("STALE");
    expect(screen.getByText("BRAIN: DEGRADED")).toBeInTheDocument();
  });

  test("does not invent a telemetry snapshot", () => {
    render(<SystemWorkspace workspace={workspace} />);
    expect(screen.getByTestId("system-telemetry-freshness")).toHaveTextContent("UNAVAILABLE");
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.getByText("NO SUBSYSTEM SNAPSHOT")).toBeInTheDocument();
  });
});
