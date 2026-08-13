import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";
import { useCharlieStore } from "../store/charlie";
import { Notification } from "./Notification";

beforeEach(() => {
  useCharlieStore.setState({ activeAlert: null });
});

describe("Notification", () => {
  test("renders nothing when no alert was emitted", () => {
    render(<Notification />);

    expect(screen.queryByLabelText("Notification")).not.toBeInTheDocument();
  });

  test("renders only alert event detail", () => {
    useCharlieStore.setState({ activeAlert: { id: "a1", severity: "warning", message: "CPU threshold exceeded" } });
    render(<Notification />);

    expect(screen.getByText("CPU threshold exceeded")).toBeInTheDocument();
    expect(screen.getByText("warning")).toBeInTheDocument();
    expect(screen.queryByText("Brute Force Attack")).not.toBeInTheDocument();
  });
});
