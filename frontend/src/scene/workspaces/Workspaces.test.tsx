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
    expect(screen.getByText("EXECUTIVE SYNTHESIS")).toBeDefined();
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

  test("TasksWorkspace renders execution plan, current progress step, and concurrent queue", () => {
    render(<TasksWorkspace workspace={{ ...mockWorkspace, type: "tasks" }} />);
    expect(screen.getByText("TASK EXECUTION WORKSPACE")).toBeDefined();
    expect(screen.getByText("EXECUTION PLAN & STATUS")).toBeDefined();
    expect(screen.getByText(/CONCURRENT TASKS/)).toBeDefined();
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
    expect(screen.getByText(/CHARLIE TERMINAL/i)).toBeDefined();
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
});
