import { render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { Calendar } from "./Calendar";

describe("Calendar", () => {
  test("shows current month and no fabricated agenda", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-13T12:00:00"));
    render(<Calendar />);

    expect(screen.getByText("August 2026")).toBeInTheDocument();
    expect(screen.getByText("No calendar events reported.")).toBeInTheDocument();
    expect(screen.queryByText("System Backup")).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});
