import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";
import { useCharlieStore } from "../store/charlie";
import { Topbar } from "./Topbar";

beforeEach(() => {
  useCharlieStore.setState({ connected: false, coreState: "idle", subsystemHealth: {} });
});

describe("Topbar", () => {
  test("does not expose a switch to a fabricated dashboard state", () => {
    render(<Topbar />);

    expect(screen.queryByRole("button", { name: "Switch dashboard view" })).not.toBeInTheDocument();
  });

  test("shows unknown runtime health instead of fabricated health and voice states", () => {
    render(<Topbar />);

    expect(screen.getByText("Status: offline")).toBeInTheDocument();
    expect(screen.getByLabelText("System status")).toHaveTextContent("System health: Unknown");
    expect(screen.getByLabelText("System status")).toHaveTextContent("Voice: Unknown");
    expect(screen.queryByText("100%")).not.toBeInTheDocument();
    expect(screen.queryByText("Online")).not.toBeInTheDocument();
  });
});
