import { describe, expect, test, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ConversationWorkspace } from "./ConversationWorkspace";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { useCharlieStore } from "../../store/charlie";
import { sendCommand } from "../../runtime/bridge";

vi.mock("../../runtime/bridge", () => ({
  sendCommand: vi.fn(),
}));

describe("ConversationWorkspace Component", () => {
  const mockWorkspace: WorkspaceInstance = {
    id: "conversation",
    presentationIntentId: "intent-conv",
    taskId: "task-conv",
    title: "CONVERSATION",
    summary: "Chat Session",
    type: "conversation",
    status: "active",
    lifecycleState: "active",
    focused: true,
    openedAt: new Date().toISOString(),
    lastFocusedAt: new Date().toISOString(),
    persistent: true,
    replayable: false,
    contentState: {},
  };

  beforeEach(() => {
    vi.clearAllMocks();
    useCharlieStore.setState({
      chatMessages: [
        { id: "m1", role: "user", text: "Hello Charlie", pending: false },
        { id: "m2", role: "charlie", text: "Greetings! How can I help you today?", pending: false },
      ],
      activeToolApproval: null,
      activities: [],
      coreState: "idle",
      connected: true,
      activeSessionId: null,
      activeSessionTitle: null,
    });
  });

  test("renders conversation messages with correct roles", () => {
    render(<ConversationWorkspace workspace={mockWorkspace} />);

    expect(screen.getByText(/CONVERSATION & DIALOGUE LOG/i)).toBeDefined();
    expect(screen.getByText("Hello Charlie")).toBeDefined();
    expect(screen.getByText("Greetings! How can I help you today?")).toBeDefined();
    expect(screen.getByText("OPERATOR")).toBeDefined();
    expect(screen.getByText("CHARLIE")).toBeDefined();
  });

  test("renders passive pending approval context without duplicate action buttons", () => {
    useCharlieStore.setState({
      activeToolApproval: {
        request_id: "req-123",
        tool_name: "run_shell_command",
        reason: "Execute directory listing",
        arguments: { command: "dir" },
        risk_class: "high",
      },
    });

    render(<ConversationWorkspace workspace={mockWorkspace} />);

    expect(screen.getByText(/Approval Required: run_shell_command/i)).toBeDefined();
    expect(screen.getByText("Execute directory listing")).toBeDefined();
    expect(screen.getByText("Detailed approval information is available in the canonical approval dialog.")).toBeDefined();
    expect(screen.queryByText("Approve Action")).toBeNull();
    expect(screen.queryByText("Reject")).toBeNull();
  });

  test("submits input text when Send button is clicked", async () => {
    const originalFetch = global.fetch;
    global.fetch = async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/session/active")) {
        return {
          ok: true,
          json: async () => ({ active_session: "canonical_sess_123" }),
        } as Response;
      }
      return {
        ok: true,
        json: async () => ({ messages: [] }),
      } as Response;
    };

    render(<ConversationWorkspace workspace={mockWorkspace} />);

    const textarea = await screen.findByPlaceholderText(/Send prompt to Charlie.../i);
    fireEvent.change(textarea, { target: { value: "Run diagnostic check" } });

    const sendBtn = screen.getByText("Send");
    fireEvent.click(sendBtn);

    // Verifies store optimistic update
    const messages = useCharlieStore.getState().chatMessages;
    expect(messages.some((m) => m.text === "Run diagnostic check")).toBe(true);

    global.fetch = originalFetch;
  });

  test("arbitrary workspace ID does not become chat session ID", async () => {
    const originalFetch = global.fetch;
    global.fetch = async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/session/active")) {
        return {
          ok: true,
          json: async () => ({ active_session: "voice_active_canonical" }),
        } as Response;
      }
      return {
        ok: true,
        json: async () => ({ messages: [] }),
      } as Response;
    };

    const arbitraryWorkspace: WorkspaceInstance = {
      ...mockWorkspace,
      id: "presentation-conversation-7f83",
    };

    render(<ConversationWorkspace workspace={arbitraryWorkspace} />);

    expect(await screen.findByText("voice_active_canonical")).toBeDefined();
    expect(screen.queryByText("presentation-conversation-7f83")).toBeNull();

    global.fetch = originalFetch;
  });

  test("never sends 'default' session while canonical session resolution is pending", async () => {
    let pendingResolve: (val: any) => void = () => {};
    const pendingPromise = new Promise((resolve) => {
      pendingResolve = resolve;
    });

    const originalFetch = global.fetch;
    global.fetch = async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/session/active")) {
        return pendingPromise as any;
      }
      return {
        ok: true,
        json: async () => ({ messages: [] }),
      } as Response;
    };

    render(<ConversationWorkspace workspace={mockWorkspace} />);

    // While pending, shows CONNECTING and textarea/send is disabled
    expect(screen.getByText(/CONNECTING.../i)).toBeDefined();
    expect(screen.queryByText("default")).toBeNull();
    const textarea = screen.getByPlaceholderText(/Connecting to active session.../i);
    expect(textarea.hasAttribute("disabled")).toBe(true);

    // Resolve active session
    pendingResolve({
      ok: true,
      json: async () => ({ active_session: "session_abc_789" }),
    });

    expect(await screen.findByText("session_abc_789")).toBeDefined();
    global.fetch = originalFetch;
  });

  test("shows authoritative session history and switches through the active-session API", async () => {
    const originalFetch = global.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    global.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url === "/api/session/active" && !init) {
        return { ok: true, json: async () => ({ active_session: "session-current" }) } as Response;
      }
      if (url === "/api/sessions") {
        return {
          ok: true,
          json: async () => ({ sessions: [
            { id: "session-current", title: "Current", updated_at: "2026-09-11T00:00:00Z" },
            { id: "session-older", title: "Older", updated_at: "2026-09-10T00:00:00Z" },
          ] }),
        } as Response;
      }
      if (url === "/api/session/active" && init?.method === "POST") {
        return { ok: true, json: async () => ({ status: "completed", result: { active_session_id: "session-older" } }) } as Response;
      }
      return { ok: true, json: async () => ({ messages: [] }) } as Response;
    };

    render(<ConversationWorkspace workspace={mockWorkspace} />);
    fireEvent.click(await screen.findByRole("button", { name: /SESSION HISTORY/ }));
    fireEvent.click(await screen.findByRole("option", { name: /Older/ }));

    await waitFor(() => expect(useCharlieStore.getState().activeSessionId).toBe("session-older"));
    expect(calls.some((call) => call.url === "/api/session/active" && call.init?.method === "POST")).toBe(true);
    global.fetch = originalFetch;
  });

  test("uses HTTP fallback alone while disconnected", async () => {
    useCharlieStore.setState({ connected: false });
    const originalFetch = global.fetch;
    const calls: Array<{ url: string; body?: string }> = [];
    global.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, body: init?.body as string | undefined });
      if (url.includes("/api/session/active")) {
        return { ok: true, json: async () => ({ active_session: "session-http" }) } as Response;
      }
      return { ok: true, json: async () => ({ messages: [] }) } as Response;
    };

    render(<ConversationWorkspace workspace={mockWorkspace} />);
    const textarea = await screen.findByPlaceholderText(/Send prompt to Charlie/i);
    fireEvent.change(textarea, { target: { value: "Fallback chat" } });
    fireEvent.click(screen.getByText("Send"));

    expect(sendCommand).not.toHaveBeenCalledWith("chat", expect.anything());
    const chatCall = calls.find((call) => call.url.includes("/chat"));
    expect(chatCall).toBeDefined();
    expect(JSON.parse(chatCall?.body ?? "{}")).toMatchObject({ text: "Fallback chat" });
    global.fetch = originalFetch;
  });

  test("marks optimistic HTTP chat failed when main rejects admission", async () => {
    useCharlieStore.setState({ connected: false });
    const originalFetch = global.fetch;
    global.fetch = async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/session/active")) {
        return { ok: true, json: async () => ({ active_session: "session-reject" }) } as Response;
      }
      return { ok: false, json: async () => ({ status: "not_found" }) } as Response;
    };

    render(<ConversationWorkspace workspace={mockWorkspace} />);
    const textarea = await screen.findByPlaceholderText(/Send prompt to Charlie/i);
    fireEvent.change(textarea, { target: { value: "Rejected chat" } });
    fireEvent.click(screen.getByText("Send"));

    expect(await screen.findByText("[failed]")).toBeDefined();
    expect(useCharlieStore.getState().chatMessages.at(-1)?.failed).toBe(true);
    global.fetch = originalFetch;
  });
});
