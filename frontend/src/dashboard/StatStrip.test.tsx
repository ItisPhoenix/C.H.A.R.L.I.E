import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";
import { useCharlieStore } from "../store/charlie";
import { StatStrip } from "./StatStrip";

beforeEach(() => {
  useCharlieStore.setState({ systemStatus: null });
});

describe("StatStrip", () => {
  test("shows unavailable instead of demo metrics without a runtime status", () => {
    render(<StatStrip />);

    expect(screen.getAllByText("Unavailable")).toHaveLength(3);
    expect(screen.queryByText("18%")).not.toBeInTheDocument();
    expect(screen.queryByText("128.4 KB/s")).not.toBeInTheDocument();
  });

  test("shows values received from the runtime", () => {
    useCharlieStore.setState({
      systemStatus: { cpu: 12.4, ram: 43.6, gpu: 7.8, netKbps: 99.2, uptimeSeconds: 3661, batteryPercent: null },
    });
    render(<StatStrip />);

    expect(screen.getByText("12%")).toBeInTheDocument();
    expect(screen.getByText("44%")).toBeInTheDocument();
    expect(screen.getByText("99.2 KB/s")).toBeInTheDocument();
  });
});
