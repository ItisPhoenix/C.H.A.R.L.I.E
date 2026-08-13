import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { Terminal } from "./Terminal";
import { useLayoutStore } from "./layoutStore";

describe("Terminal", () => {
  test("starts a real shell session and renders its returned output", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ session_id: "s1", status: "running", output: "ready\n" }), { status: 200 }))
      .mockResolvedValue(new Response(JSON.stringify({ session_id: "s1", status: "running", output: "ready\n" }), { status: 200 })));
    useLayoutStore.getState().open("terminal");
    render(<Terminal />);

    fireEvent.click(screen.getByRole("button", { name: "Start terminal" }));
    await waitFor(() => expect(screen.getByText("ready")).toBeInTheDocument());
    expect(screen.queryByText("Charlie OS v1.0.0")).not.toBeInTheDocument();
  });
});
