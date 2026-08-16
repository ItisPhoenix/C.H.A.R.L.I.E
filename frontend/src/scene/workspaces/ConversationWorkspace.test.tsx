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

  test("submits input text when Send button is clicked", () => {
    render(<ConversationWorkspace workspace={mockWorkspace} />);

    const textarea = screen.getByPlaceholderText(/Send prompt to Charlie.../i);
    fireEvent.change(textarea, { target: { value: "Run diagnostic check" } });

    const sendBtn = screen.getByText("Send");
    fireEvent.click(sendBtn);

    // Verifies store optimistic update
    const messages = useCharlieStore.getState().chatMessages;
    expect(messages.some((m) => m.text === "Run diagnostic check")).toBe(true);
  });
});
