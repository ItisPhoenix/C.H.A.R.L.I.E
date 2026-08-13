import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { ToolsGrid } from "./ToolsGrid";
import { useLayoutStore } from "./layoutStore";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ToolsGrid", () => {
  test("renders the live tool roster instead of a static tool list", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        tools: [{ name: "file_read", description: "Read a file", owner: "tools", risk_class: "safe" }],
      }),
    }));
    useLayoutStore.getState().open("tools");

    render(<ToolsGrid />);

    await waitFor(() => expect(screen.getByText("file_read")).toBeInTheDocument());
    expect(screen.queryByText("VirusTotal")).not.toBeInTheDocument();
  });

  test("shows an honest unavailable state when tool roster cannot be loaded", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    useLayoutStore.getState().open("tools");

    render(<ToolsGrid />);

    await waitFor(() => expect(screen.getByText("No tools reported.")).toBeInTheDocument());
  });
});
