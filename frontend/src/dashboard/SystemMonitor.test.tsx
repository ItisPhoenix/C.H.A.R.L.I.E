import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";
import { useCharlieStore } from "../store/charlie";
import { SystemMonitor } from "./SystemMonitor";

beforeEach(() => {
  useCharlieStore.setState({ systemStatus: null, netHistory: [] });
});

describe("SystemMonitor", () => {
  test("renders unavailable state without fabricated metric values", () => {
    render(<SystemMonitor />);

    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    expect(screen.queryByText("18%")).not.toBeInTheDocument();
    expect(screen.queryByText("31%")).not.toBeInTheDocument();
    expect(screen.queryByText("2h 34m 16s")).not.toBeInTheDocument();
  });

  test("renders real runtime metrics when present", () => {
    useCharlieStore.setState({
      systemStatus: { cpu: 12.4, ram: 43.6, gpu: 7.8, netKbps: 99.2, uptimeSeconds: 3661, batteryPercent: null },
      netHistory: [12, 30, 99],
    });
    render(<SystemMonitor />);

    expect(screen.getByText("12%")).toBeInTheDocument();
    expect(screen.getByText("44%")).toBeInTheDocument();
    expect(screen.getByText("99.2 KB/s")).toBeInTheDocument();
    expect(screen.getByText("1h 1m")).toBeInTheDocument();
    expect(screen.queryByText("Battery")).not.toBeInTheDocument();
  });
});
