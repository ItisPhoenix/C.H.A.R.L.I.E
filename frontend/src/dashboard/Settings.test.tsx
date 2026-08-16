import { describe, expect, test, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Settings } from "./Settings";

describe("Settings Component", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url === "/api/config") {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                fields: [
                  { key: "ASSISTANT_NAME", label: "Assistant Name", group: "General", type: "str", secret: false, restart: null, value: "C.H.A.R.L.I.E.", is_set: true },
                  { key: "API_KEY", label: "Secret API Key", group: "Models", type: "str", secret: true, restart: null, value: "", is_set: true },
                  { key: "KOKORO_VOICE", label: "Voice Profile", group: "Voice & Speech", type: "str", secret: false, restart: null, value: "af_heart", is_set: true },
                ],
              }),
          });
        }
        if (url === "/api/capabilities") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ tools: [{ name: "shell_execute" }], runtime: {} }),
          });
        }
        if (url === "/api/models") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ models: ["gemini-2.5-pro", "gemini-2.5-flash"] }),
          });
        }
        if (url === "/api/audit") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ entries: [] }),
          });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      })
    );
  });

  test("renders settings categories sidebar including 15 standard categories", async () => {
    render(<Settings embed={true} />);

    expect(await screen.findByRole("button", { name: /^General/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Voice/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Models/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Map/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Pet/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Audit & Diagnostics/i })).toBeDefined();
  });

  test("filters settings when clicking category tab", async () => {
    render(<Settings embed={true} />);

    const voiceBtn = await screen.findByRole("button", { name: /^Voice/i });
    fireEvent.click(voiceBtn);

    expect(await screen.findByText("Voice Profile")).toBeDefined();
  });
});
