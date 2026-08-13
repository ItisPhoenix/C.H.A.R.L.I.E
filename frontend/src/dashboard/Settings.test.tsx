import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { Settings } from "./Settings";
import { useLayoutStore } from "./layoutStore";

afterEach(() => vi.restoreAllMocks());

describe("Settings", () => {
  test("renders real configuration fields from the settings API", async () => {
    useLayoutStore.getState().open("settings");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ fields: [{ key: "WAKE_WORD_ENABLED", label: "Wake word", group: "Voice", type: "boolean", secret: false, restart: "voice", value: true }] }),
    }));

    render(<Settings />);

    await waitFor(() => expect(screen.getByLabelText("Wake word")).toBeInTheDocument());
  });

  test("renders discovered cloud models as a real selector", async () => {
    useLayoutStore.getState().open("settings");
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/config")) return Promise.resolve({ ok: true, json: async () => ({ fields: [{ key: "LLM_MODEL", label: "LLM model", group: "LLM", type: "str", secret: false, restart: "process", value: "model-a" }] }) });
      if (url.endsWith("/api/models")) return Promise.resolve({ ok: true, json: async () => ({ active_model: "model-a", models: ["model-a", "model-b"] }) });
      return Promise.resolve({ ok: true, json: async () => ({ entries: [], tools: [] }) });
    }));

    render(<Settings />);

    await waitFor(() => expect(screen.getByRole("combobox", { name: "LLM model" })).toBeInTheDocument());
    expect(screen.getByRole("option", { name: "model-b" })).toBeInTheDocument();
  });
});
