import { describe, expect, test, beforeEach, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { TaskSwitcher } from "./TaskSwitcher";
import { useCharlieStore } from "../store/charlie";
import { useWorkspaceStore } from "../layout/workspaceStore";
import { sendCommand } from "../runtime/bridge";

vi.mock("../runtime/bridge", () => ({ sendCommand: vi.fn() }));

describe("TaskSwitcher Component", () => {
  beforeEach(() => {
    useCharlieStore.setState({ tasks: {} });
    useWorkspaceStore.setState({ workspaces: {}, activeWorkspaceId: null, recentWorkspaces: [] });
    vi.mocked(sendCommand).mockClear();
  });

  test("hidden when 0 or 1 task exists", () => {
    const { container } = render(<TaskSwitcher />);
    expect(container.firstChild).toBeNull();
  });

  test("renders switcher toolbar when multiple tasks exist (> 1)", () => {
    useCharlieStore.setState({
      tasks: {
        t1: {
          id: "t1",
          title: "Surveillance Scan",
          status: "running",
          currentStep: 2,
          totalSteps: 4,
          progress: 0.5,
        },
        t2: {
          id: "t2",
          title: "Data Ingestion",
          status: "running",
          currentStep: 1,
          totalSteps: 3,
          progress: 0.33,
        },
      },
    });

    render(<TaskSwitcher />);
    expect(screen.getByText("TASKS [2]")).toBeDefined();
    expect(screen.getByText("Surveillance Scan")).toBeDefined();
    expect(screen.getByText("Data Ingestion")).toBeDefined();
  });

  test("requests runtime focus without resolving workspace locally", () => {
    useCharlieStore.setState({
      tasks: {
        t1: { id: "t1", title: "Surveillance Scan", status: "running", currentStep: 1, totalSteps: 2 },
        t2: { id: "t2", title: "Data Ingestion", status: "running", currentStep: 1, totalSteps: 3 },
      },
    });
    useWorkspaceStore.setState({
      workspaces: {
        ws1: {
          id: "ws1", type: "tasks", presentationIntentId: "ws1", taskId: "t1", title: "Scan", summary: "",
          status: "active", lifecycleState: "active", focused: true, openedAt: "", lastFocusedAt: "",
          persistent: false, replayable: true, contentState: {},
        },
        ws2: {
          id: "ws2", type: "tasks", presentationIntentId: "ws2", taskId: "t2", title: "Ingest", summary: "",
          status: "active", lifecycleState: "minimized", focused: false, openedAt: "", lastFocusedAt: "",
          persistent: false, replayable: true, contentState: {},
        },
      },
      activeWorkspaceId: "ws1",
    });

    render(<TaskSwitcher />);
    fireEvent.click(screen.getByTitle("Switch to task: Data Ingestion"));
    expect(sendCommand).toHaveBeenCalledWith("presentation_command", {
      action: "focus_task",
      task_id: "t2",
    });
    expect(useWorkspaceStore.getState().activeWorkspaceId).toBe("ws1");
  });

  test("excludes completed zero-step history from active task rail", () => {
    useCharlieStore.setState({
      tasks: {
        completed: { id: "completed", title: "CPU query", status: "completed", currentStep: 0, totalSteps: 0 },
        running: { id: "running", title: "Research", status: "running", currentStep: 1, totalSteps: 3 },
        queued: { id: "queued", title: "Queued work", status: "queued", currentStep: 0, totalSteps: 2 },
      },
    });
    render(<TaskSwitcher />);
    expect(screen.getByText("TASKS [2]")).toBeDefined();
    expect(screen.queryByText("CPU query")).toBeNull();
    expect(screen.getByText("Research")).toBeDefined();
  });
});
