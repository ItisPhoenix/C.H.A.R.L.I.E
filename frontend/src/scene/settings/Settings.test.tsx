import { describe, expect, test, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Settings } from "./Settings";

let fetchMock: ReturnType<typeof vi.fn>;

describe("Settings Component", () => {
  beforeEach(() => {
    fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url === "/api/config") {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                fields: [
                  { key: "ASSISTANT_NAME", label: "Assistant Name", group: "General", type: "str", secret: false, restart: null, value: "CHARLIE", is_set: true },
                  { key: "API_KEY", label: "Secret API Key", group: "Models", type: "str", secret: true, restart: null, value: "", is_set: true },
                  { key: "KOKORO_VOICE", label: "Voice Profile", group: "Voice & Speech", type: "str", secret: false, restart: null, value: "af_heart", is_set: true },
                  { key: "CONTEXT_WINDOW", label: "Context Window", group: "Chat Behavior", type: "int", secret: false, restart: null, value: 8192, is_set: true },
                  { key: "HUD_INVOKE_HOTKEY", label: "HUD Invoke Hotkey", group: "Surfaces", type: "str", secret: false, restart: "process", value: "ctrl+space", is_set: true },
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
        if (url === "/api/mcp/servers") {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                servers: [
                  {
                    name: "filesystem",
                    command: "npx",
                    args: ["-y", "@modelcontextprotocol/server-filesystem"],
                    running: true,
                    status: "connected",
                    tools_count: 2,
                    tools: [{ name: "read_file", description: "Read a file" }],
                  },
                ],
              }),
          });
        }
        if (url === "/api/memory/items" || url.startsWith("/api/memory/search")) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                items: [
                  { id: "mem_1", category: "preference", content: "Prefers dark mode", created_at: "2026-08-17T00:00:00Z" },
                ],
              }),
          });
        }
        if (url === "/api/privacy/summary") {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                total_bytes: 2048,
                total_formatted: "2.0 KB",
                categories: {
                  transcripts: { name: "Chat Transcripts", bytes: 1024, formatted: "1.0 KB" },
                  browser: { name: "Browser Cache", bytes: 1024, formatted: "1.0 KB" },
                },
              }),
          });
        }
        if (url === "/api/developer/diagnostics") {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                developer_mode_enabled: true,
                diagnostics: {
                  tasks: [],
                  leases: { desktop: "session-1" },
                  system: { uptime_seconds: 120, active_threads: 4, active_ws_connections: 1 },
                },
              }),
          });
        }
        if (url === "/api/developer/logs?limit=50") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ lines: ["[INFO] System ready"], total_lines: 1 }),
          });
        }
        if (url === "/api/doctor/diagnose") {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                timestamp: 1723900000,
                total_checks: 17,
                is_healthy: true,
                warnings_count: 0,
                errors_count: 0,
                checks: [
                  {
                    check_id: "config_validity",
                    category: "config",
                    status: "ok",
                    severity: "low",
                    summary: "Configuration valid and loaded",
                    evidence: "Loaded configuration.",
                    repair_available: false,
                    requires_approval: false,
                  },
                  {
                    check_id: "capability_leases",
                    category: "leases",
                    status: "warning",
                    severity: "medium",
                    summary: "Orphan lease found",
                    evidence: "Orphan lease on desktop.",
                    repair_available: true,
                    repair_id: "repair_stale_leases",
                    requires_approval: false,
                  },
                ],
              }),
          });
        }
        if (url === "/api/self/query") {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                is_self_question: true,
                answer: "Configured to use gpt-4o via openai.",
                evidence_sources: ["runtime.model", "capability.registry"],
              }),
          });
        }
        if (url === "/api/doctor/repair") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ success: true, message: "Cleared stale capability leases." }),
          });
        }
        if (url === "/api/audit") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ entries: [] }),
          });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      });
    vi.stubGlobal("fetch", fetchMock);
  });

  test("renders settings categories sidebar including standard categories", async () => {
    render(<Settings />);

    expect(await screen.findByRole("button", { name: /^General/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Audio/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Models/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Map/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Pet/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Tools \/ MCP/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Memory/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Privacy/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Developer/i })).toBeDefined();
    expect(await screen.findByRole("button", { name: /^Audit & Diagnostics/i })).toBeDefined();
  });

  test("exposes one authoritative settings surface with Audio, not Voice, as top-level category", async () => {
    render(<Settings />);

    expect(await screen.findByTestId("authoritative-settings")).toBeDefined();
    for (const category of ["General", "Appearance", "Audio", "Pet", "Privacy", "Tools / MCP", "Integrations", "System"]) {
      expect(screen.getByRole("button", { name: new RegExp(`^${category.replace("/", "\\/")}$`, "i") })).toBeDefined();
    }
    expect(screen.queryByRole("button", { name: /^Voice$/i })).toBeNull();
  });

  test("filters settings when clicking category tab", async () => {
    render(<Settings />);

    const audioBtn = await screen.findByRole("button", { name: /^Audio/i });
    fireEvent.click(audioBtn);

    expect(await screen.findByText("Voice Profile")).toBeDefined();
  });

  test("maps voice fields into Audio and keeps configured secrets masked", async () => {
    render(<Settings />);

    fireEvent.click(await screen.findByRole("button", { name: /^Audio/i }));
    expect(await screen.findByText("Voice Profile")).toBeDefined();
    expect(screen.queryByDisplayValue("•••••••• (configured)")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^All/i }));
    const secret = await screen.findByLabelText("Secret API Key");
    expect(secret).toHaveAttribute("type", "password");
    expect(secret).toHaveAttribute("placeholder", "•••••••• (configured)");
  });

  test("routes backend General and HUD groups and explains scene-owned Appearance", async () => {
    render(<Settings />);

    fireEvent.click(await screen.findByRole("button", { name: /^General/i }));
    expect(await screen.findByText("Context Window")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: /^HUD/i }));
    expect(await screen.findByText("HUD Invoke Hotkey")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: /^Appearance/i }));
    expect(await screen.findByText(/Appearance follows CharlieScene theme tokens/i)).toBeDefined();
  });

  test("saves only modified fields and reports failed saves", async () => {
    render(<Settings />);
    const assistantName = await screen.findByLabelText("Assistant Name");
    fireEvent.change(assistantName, { target: { value: "CHARLIE TEST" } });

    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    await screen.findByText("Saved. Reload required settings when ready.");
    const saveCall = fetchMock.mock.calls.find(
      ([url, init]) => url === "/api/config" && (init as RequestInit | undefined)?.method === "POST",
    );
    expect(saveCall).toBeDefined();
    expect(JSON.parse(String((saveCall?.[1] as RequestInit).body))).toEqual({ ASSISTANT_NAME: "CHARLIE TEST" });

    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/config" && init?.method === "POST") return Promise.resolve({ ok: false });
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });
    fireEvent.change(assistantName, { target: { value: "CHARLIE FAILED" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(await screen.findByText("Settings save failed.")).toBeDefined();
    expect(screen.queryByText("Saved. Reload required settings when ready.")).toBeNull();
  });

  test("displays MCP servers when Tools / MCP category is selected", async () => {
    render(<Settings />);

    const mcpBtn = await screen.findByRole("button", { name: /^Tools \/ MCP/i });
    fireEvent.click(mcpBtn);

    expect(await screen.findByText("Model Context Protocol (MCP) Servers")).toBeDefined();
    expect(await screen.findByText("filesystem")).toBeDefined();
    expect(await screen.findByText("read_file")).toBeDefined();
  });

  test("displays Memory items and controls when Memory category is selected", async () => {
    render(<Settings />);

    const memBtn = await screen.findByRole("button", { name: /^Memory/i });
    fireEvent.click(memBtn);

    expect(await screen.findByText("Knowledge & Memory Store")).toBeDefined();
    expect(await screen.findByText("Prefers dark mode")).toBeDefined();
  });

  test("displays Privacy and retention summary when Privacy category is selected", async () => {
    render(<Settings />);

    const privBtn = await screen.findByRole("button", { name: /^Privacy/i });
    fireEvent.click(privBtn);

    expect(await screen.findByText("Storage Usage & Data Retention")).toBeDefined();
    expect(await screen.findByText("Total Usage: 2.0 KB")).toBeDefined();
  });

  test("displays Developer diagnostics and logs when Developer category is selected", async () => {
    render(<Settings />);

    const devBtn = await screen.findByRole("button", { name: /^Developer/i });
    fireEvent.click(devBtn);

    expect(await screen.findByText("Developer Diagnostics & Live Telemetry")).toBeDefined();
    expect(await screen.findByText("[INFO] System ready")).toBeDefined();
    expect(await screen.findByText("Charlie Doctor Health Diagnostics")).toBeDefined();
    expect(await screen.findByText("Self-Knowledge & Code Truth Explorer")).toBeDefined();
  });

  test("runs self-knowledge query when submitted", async () => {
    render(<Settings />);

    const devBtn = await screen.findByRole("button", { name: /^Developer/i });
    fireEvent.click(devBtn);

    const input = await screen.findByPlaceholderText("Ask Charlie about models, capabilities, leases, or code locations...");
    const askBtn = await screen.findByRole("button", { name: /^Ask$/i });

    fireEvent.change(input, { target: { value: "What model are you configured to use?" } });
    fireEvent.click(askBtn);

    expect(await screen.findByText("Configured to use gpt-4o via openai.")).toBeDefined();
    expect(await screen.findByText("runtime.model")).toBeDefined();
  });
});
