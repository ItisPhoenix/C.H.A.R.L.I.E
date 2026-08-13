import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { Calendar } from "./Calendar";
import { useLayoutStore } from "./layoutStore";

describe("Calendar", () => {
  test("renders events from the local calendar API", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-13T12:00:00"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ events: [{ id: "1", title: "Dentist", start_at: "2026-08-13T14:00:00+05:30" }] }), { status: 200 })));
    useLayoutStore.getState().open("calendar");
    render(<Calendar />);
    vi.useRealTimers();

    expect(screen.getByText("August 2026")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Dentist")).toBeInTheDocument());
    expect(screen.queryByText("System Backup")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add reminder" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit Dentist" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete Dentist" })).toBeInTheDocument();
  });
});
