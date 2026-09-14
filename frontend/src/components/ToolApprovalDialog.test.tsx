import { beforeEach, describe, expect, test, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ToolApprovalDialog } from "./ToolApprovalDialog";
import { useCharlieStore } from "../store/charlie";
import { sendCommand } from "../runtime/bridge";

vi.mock("../runtime/bridge", () => ({
  sendCommand: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  useCharlieStore.setState({ activeToolApproval: null });
});

describe("ToolApprovalDialog", () => {
  test("renders nothing without a pending approval", () => {
    render(<ToolApprovalDialog />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("approves through the normal bridge command and waits for runtime resolution", () => {
    useCharlieStore.setState({
      activeToolApproval: {
        request_id: "approval-1",
        tool_name: "shell_execute",
        reason: "The command needs approval.",
        arguments: { command: "python --version" },
        risk_class: "security_sensitive",
      },
    });

    render(<ToolApprovalDialog />);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("The command needs approval.")).toBeInTheDocument();
    expect(screen.getAllByText(/CONTEXT UNAVAILABLE/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/command: python --version/)).toBeNull();
    expect(screen.getByText(/Hidden tool arguments and secrets are not rendered/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(sendCommand).toHaveBeenCalledWith("tool_approve", { request_id: "approval-1" });
    expect(useCharlieStore.getState().activeToolApproval?.request_id).toBe("approval-1");
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();

    useCharlieStore.getState().applyEvent({ type: "tool_approval_resolved", payload: { request_id: "approval-1" } });
    expect(useCharlieStore.getState().activeToolApproval).toBeNull();
  });

  test("renders only supplied canonical approval context", () => {
    useCharlieStore.setState({
      activeToolApproval: {
        request_id: "approval-context",
        tool_name: "browser_navigate",
        reason: "Open the supplied destination.",
        arguments: { url: "https://example.com" },
        risk_class: "safe",
        session_id: "session-1",
        turn_id: "turn-1",
        task_id: "task-1",
      },
    });

    render(<ToolApprovalDialog />);

    expect(screen.getByText("session-1 · TURN turn-1 · TASK task-1", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("CONTEXT UNAVAILABLE — no sanitized target or scope was supplied.")).toBeInTheDocument();
    expect(screen.queryByText("https://example.com")).toBeNull();
  });

  test("declines the active request through the normal bridge command", () => {
    useCharlieStore.setState({
      activeToolApproval: {
        request_id: "approval-2",
        tool_name: "shell_execute",
        reason: "Decline this command.",
        arguments: { command: "Remove-Item file.txt" },
        risk_class: "destructive",
      },
    });

    render(<ToolApprovalDialog />);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    expect(sendCommand).toHaveBeenCalledWith("tool_reject", { request_id: "approval-2" });
    expect(useCharlieStore.getState().activeToolApproval?.request_id).toBe("approval-2");
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });

  test("rapid approval clicks send one command", () => {
    useCharlieStore.setState({
      activeToolApproval: {
        request_id: "approval-3",
        tool_name: "shell_execute",
        reason: "Run once.",
        arguments: { command: "echo safe" },
        risk_class: "security_sensitive",
      },
    });

    render(<ToolApprovalDialog />);
    const approve = screen.getByRole("button", { name: "Approve" });
    fireEvent.click(approve);
    fireEvent.click(approve);

    expect(sendCommand).toHaveBeenCalledTimes(1);
  });
});
