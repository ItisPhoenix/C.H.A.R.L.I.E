import { describe, expect, test, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ConversationWorkspace } from "./ConversationWorkspace";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { useCharlieStore } from "../../store/charlie";

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
    useCharlieStore.setState({
      chatMessages: [
        { id: "m1", role: "user", text: "Hello Charlie", pending: false },
        { id: "m2", role: "charlie", text: "Greetings! How can I help you today?", pending: false },
      ],
      activeToolApproval: null,
      activities: [],
      coreState: "idle",
      connected: true,
    });
  });

  test("renders conversation messages with correct roles", () => {
    render(<ConversationWorkspace workspace={mockWorkspace} />);

    expect(screen.getByText(/CONVERSATION & DIALOGUE LOG/i)).toBeDefined();
    expect(screen.getByText("Hello Charlie")).toBeDefined();
    expect(screen.getByText("Greetings! How can I help you today?")).toBeDefined();
    expect(screen.getByText("OPERATOR")).toBeDefined();
    expect(screen.getByText("C.H.A.R.L.I.E.")).toBeDefined();
  });

  test("renders pending tool approvals and action buttons", () => {
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
    expect(screen.getByText("Approve Action")).toBeDefined();
    expect(screen.getByText("Reject")).toBeDefined();
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
});
