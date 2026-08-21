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

  test("approves the active request through the normal bridge command", () => {
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
    expect(screen.getByText(/command: python --version/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Approve & Run" }));

    expect(sendCommand).toHaveBeenCalledWith("tool_approve", { request_id: "approval-1" });
    expect(useCharlieStore.getState().activeToolApproval).toBeNull();
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
    fireEvent.click(screen.getByRole("button", { name: "Decline" }));

    expect(sendCommand).toHaveBeenCalledWith("tool_reject", { request_id: "approval-2" });
    expect(useCharlieStore.getState().activeToolApproval).toBeNull();
  });
});
