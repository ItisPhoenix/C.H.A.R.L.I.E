import { beforeEach, describe, expect, test, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { RecentWorkspacesModal } from "./RecentWorkspacesModal";
import { useWorkspaceStore } from "./workspaceStore";

describe("RecentWorkspacesModal", () => {
  beforeEach(() => {
    useWorkspaceStore.setState({
      workspaces: {},
      activeWorkspaceId: null,
      recentWorkspaces: [{
        id: "recent-1",
        type: "research",
        title: "Research result",
        summary: "Grounded summary",
        taskId: null,
        closedAt: new Date().toISOString(),
        contentState: {},
      }],
    });
  });

  test("renders recent entries as keyboard-accessible restore buttons", () => {
    const onClose = vi.fn();
    render(<RecentWorkspacesModal isOpen onClose={onClose} />);

    const restore = screen.getByRole("button", { name: /Research result.*Restore/i });
    expect(restore).toBeInTheDocument();
    fireEvent.click(restore);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(useWorkspaceStore.getState().activeWorkspaceId).toBe("recent-1");
  });

  test("Escape closes modal", () => {
    const onClose = vi.fn();
    render(<RecentWorkspacesModal isOpen onClose={onClose} />);

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
