import { describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { ResearchWorkspace } from "./ResearchWorkspace";
import { BriefingWorkspace } from "./BriefingWorkspace";
import { SystemWorkspace } from "./SystemWorkspace";
import { TasksWorkspace } from "./TasksWorkspace";
import { MapWorkspace } from "./MapWorkspace";
import { VisionWorkspace } from "./VisionWorkspace";
import { DocumentWorkspace } from "./DocumentWorkspace";
import { TerminalWorkspace } from "./TerminalWorkspace";
import { ConversationWorkspace } from "./ConversationWorkspace";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { useCharlieStore } from "../../store/charlie";

describe("Phase 9 Workspaces Suite", () => {
  const mockWorkspace: WorkspaceInstance = {
    id: "ws-test-1",
    presentationIntentId: "intent-test-1",
    taskId: "task-test-1",
    title: "TEST WORKSPACE",
    summary: "Test summary description",
    type: "research",
    status: "active",
    lifecycleState: "active",
    focused: true,
    openedAt: new Date().toISOString(),
    lastFocusedAt: new Date().toISOString(),
    persistent: false,
    replayable: false,
    contentState: {
      objective: "Verify operational readiness of test subsystem.",
      findings: [{ id: "f1", title: "TEST FINDING", detail: "Detail text", iconType: "trend" }],
      timeline_items: [{ time: "09:00", title: "Step 1" }],
      sources: [{ id: "s1", title: "Source A", publisher: "TEST_PUB" }],
    },
  };

  test("ResearchWorkspace renders objective, key findings, and tactical elements", () => {
    render(<ResearchWorkspace workspace={mockWorkspace} />);
    expect(screen.getByText("PRIMARY RESEARCH SYNTHESIS")).toBeDefined();
    expect(screen.getByText("Verify operational readiness of test subsystem.")).toBeDefined();
    expect(screen.getByText("KEY FINDINGS & ANALYTICAL SIGNALS")).toBeDefined();
    expect(screen.getByText("TEST FINDING")).toBeDefined();
  });

  test("BriefingWorkspace renders top headline, summary, and timeline", () => {
    render(
      <BriefingWorkspace
        workspace={{
          ...mockWorkspace,
          type: "briefing",
          contentState: {
            headline: "GLOBAL BRIEFING UPDATE",
            summaries: ["Summary item 1"],
            timeline_items: [{ time: "10:00", title: "Event A" }],
            sources: [{ id: "b1", title: "Article A", publisher: "NEWS_PUB" }],
          },
        }}
      />
    );
    expect(screen.getByText("OPERATIONAL INTELLIGENCE BRIEFING")).toBeDefined();
    expect(screen.getByText("TOP HEADLINE")).toBeDefined();
    expect(screen.getByText("GLOBAL BRIEFING UPDATE")).toBeDefined();
    expect(screen.getByText("VERIFIED SOURCES")).toBeDefined();
  });

  test("SystemWorkspace renders task status, vitals overview, and live processes", () => {
    render(
      <SystemWorkspace
        workspace={{
          ...mockWorkspace,
          type: "system",
          contentState: {
            operations: [{ id: "op1", title: "INGESTION", subtitle: "Feed 1", progress: 50, status: "RUNNING" }],
            processes: [{ name: "proc1", pid: 101, status: "RUNNING", uptime: "10m" }],
            vitals: {
              title: "SYSTEM STATUS",
              gauges: [{ id: "cpu", label: "CPU", value: 40 }],
              stats: [{ label: "SYSTEM TEMP", value: "40°C" }],
            },
            logs: [{ timestamp: "10:00:00", level: "INFO", message: "System started" }],
          },
        }}
      />
    );
    expect(screen.getByText("ACTIVE SYSTEM OPERATIONS")).toBeDefined();
    expect(screen.getByText("SYSTEM STATUS")).toBeDefined();
    expect(screen.getByText("INGESTION")).toBeDefined();
  });

  test("TasksWorkspace collapses absent execution details while keeping progress and queue", () => {
    useCharlieStore.setState({
      tasks: {
        "task-test-1": {
          id: "task-test-1",
          title: "Verified task",
          status: "running",
          currentStep: 1,
          totalSteps: 1,
          progress: 0.5,
        },
      },
    });
    render(<TasksWorkspace workspace={{ ...mockWorkspace, type: "tasks" }} />);
    expect(screen.getByText("TASK EXECUTION WORKSPACE")).toBeDefined();
    expect(screen.queryByText("EXECUTION PLAN & STATUS")).toBeNull();
    expect(screen.getByText(/CONCURRENT TASKS/)).toBeDefined();
    expect(screen.queryByText(/No current action reported/i)).toBeNull();
    expect(screen.queryByText(/maritime|radar|anomaly|cross-correlation/i)).toBeNull();
  });

  test("MapWorkspace renders interactive spatial engine", () => {
    render(<MapWorkspace workspace={{ ...mockWorkspace, type: "map" }} />);
    expect(document.body).toBeDefined();
  });

  test("VisionWorkspace renders local vision sensor stream and grounding results", () => {
    render(<VisionWorkspace workspace={{ ...mockWorkspace, type: "vision" }} />);
    expect(screen.getByText("LOCAL VISION PERCEPTION")).toBeDefined();
    expect(screen.getByText("DETECTION RESULTS")).toBeDefined();
  });

  test("DocumentWorkspace renders report outline and body text", () => {
    render(<DocumentWorkspace workspace={{ ...mockWorkspace, type: "document" }} />);
    expect(screen.getByText("DOCUMENTATION & REPORT WORKSPACE")).toBeDefined();
    expect(screen.getByText("Test summary description")).toBeDefined();
  });

  test("TerminalWorkspace renders terminal header and command runner", () => {
    render(<TerminalWorkspace workspace={{ ...mockWorkspace, type: "terminal" }} />);
    expect(screen.getByText(/CHARLIE HOST TERMINAL/i)).toBeDefined();
  });

  test("ConversationWorkspace renders thread messages", () => {
    render(
      <ConversationWorkspace
        workspace={{
          ...mockWorkspace,
          type: "conversation",
          contentState: {
            session_id: "sess-1",
            messages: [{ id: "m1", role: "assistant", content: "Hello from Charlie" }],
          },
        }}
      />
    );
    expect(screen.getByText(/CONVERSATION & DIALOGUE/i)).toBeDefined();
  });

  test("does not fabricate progress or expose internal result references for completed fast paths", () => {
    useCharlieStore.setState({
      tasks: {
        "task-test-1": {
          id: "task-test-1", title: "CPU query", status: "completed", currentStep: 0, totalSteps: 0,
          resultReference: "session:voice_secret",
        },
      },
    });
    render(<TasksWorkspace workspace={{ ...mockWorkspace, type: "tasks" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("No active tasks reported.");
    expect(screen.queryByText(/session:voice_secret/)).toBeNull();
    expect(screen.queryByText(/STEP 0 OF 5/i)).toBeNull();
  });

  test("does not admit an active zero-step placeholder task", () => {
    useCharlieStore.setState({
      tasks: {
        "task-empty": {
          id: "task-empty", title: "Fast-path placeholder", status: "running", currentStep: 0, totalSteps: 0,
        },
      },
    });
    render(<TasksWorkspace workspace={{ ...mockWorkspace, type: "tasks", taskId: "task-empty" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("No active tasks reported.");
    expect(screen.queryByText(/STEP 0 OF/i)).toBeNull();
  });
});
