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
    expect(screen.getByText("RESEARCH WORKSPACE")).toBeDefined();
    expect(screen.getAllByText("Verify operational readiness of test subsystem.").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("KEY FINDINGS & ANALYTICAL SIGNALS")).toBeDefined();
    expect(screen.getByText("TEST FINDING")).toBeDefined();
    expect(screen.getByTestId("research-grounded-signal")).toHaveTextContent("FINDINGS");
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
    expect(screen.getByText("BRIEFING / NEWS")).toBeDefined();
    expect(screen.getByText("TOP HEADLINE")).toBeDefined();
    expect(screen.getByText("GLOBAL BRIEFING UPDATE")).toBeDefined();
    expect(screen.getByText("SOURCE FEED")).toBeDefined();
    expect(screen.getByText("GROUNDED NEWS SIGNAL")).toBeDefined();
  });

  test("SystemWorkspace renders task status, vitals overview, and live processes", () => {
    useCharlieStore.setState({
      tasks: {
        "system-task": {
          id: "system-task",
          title: "INGESTION",
          status: "running",
          currentStep: 1,
          totalSteps: 2,
          origin: "background",
          progress: 0.5,
        },
      },
    });
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
    expect(screen.getByText("SYSTEM STATUS")).toBeDefined();
    expect(screen.getByText("ACTIVE TASKS")).toBeDefined();
    expect(screen.getAllByText("INGESTION").length).toBeGreaterThan(0);
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
    expect(screen.getByText("NO LIVE MEDIA AVAILABLE")).toBeDefined();
    expect(screen.queryByText("BUTTON [Submit]")).toBeNull();
  });

  test("VisionWorkspace renders canonical observation metadata without fabricating media", () => {
    useCharlieStore.setState({
      visionObservation: {
        sessionId: "vision-session",
        uiaCount: 4,
        ocrCount: 2,
        observedAt: "2026-09-14T10:02:00.000Z",
      },
    });
    render(<VisionWorkspace workspace={{ ...mockWorkspace, type: "vision" }} />);

    expect(screen.getByTestId("vision-observation-metadata")).toHaveTextContent("OBSERVED UIA 4 · OCR 2");
    expect(screen.getByText("NO LIVE MEDIA AVAILABLE")).toBeDefined();
  });

  test("VisionWorkspace renders canonical desktop frame and mark metadata without inferred boxes", () => {
    useCharlieStore.setState({
      desktopFrame: {
        imageUrl: "data:image/png;base64,c2NyZWVu",
        sessionId: "vision-session",
        marks: [{ markId: 7, name: "Save" }],
        capturedAt: "2026-09-14T10:03:00.000Z",
      },
    });
    render(<VisionWorkspace workspace={{ ...mockWorkspace, type: "vision" }} />);

    expect(screen.getByAltText("Perception Frame")).toBeDefined();
    expect(screen.getByTestId("vision-mark")).toHaveTextContent("MARK 7: Save");
  });

  test("VisionWorkspace renders only supplied grounding payload", () => {
    render(<VisionWorkspace workspace={{
      ...mockWorkspace,
      type: "vision",
      contentState: {
        image_url: "/observations/frame.png",
        bounding_box_coordinate_space: "percent",
        frame_width: 1920,
        frame_height: 1080,
        bounding_boxes: [{ id: "real-1", label: "REAL CONTROL", confidence: 0.8, box: [10, 20, 30, 40] }],
      },
    }} />);

    expect(screen.getByText("REAL CONTROL")).toBeDefined();
    expect(screen.queryByText("DESKTOP WINDOW [Editor]")).toBeNull();
  });

  test("VisionWorkspace ignores malformed grounding entries", () => {
    render(<VisionWorkspace workspace={{
      ...mockWorkspace,
      type: "vision",
      contentState: {
        bounding_boxes: [
          { id: "bad", label: "MALFORMED", box: [0, 1, 2] },
          { id: "valid", label: "VALID REGION", confidence: 0.5, box: [10, 20, 30, 40] },
        ],
        bounding_box_coordinate_space: "percent",
        frame_width: 1920,
        frame_height: 1080,
      },
    }} />);

    expect(screen.getByText("VALID REGION")).toBeDefined();
    expect(screen.queryByText("MALFORMED")).toBeNull();
  });

  test("DocumentWorkspace renders report outline and body text", () => {
    render(<DocumentWorkspace workspace={{ ...mockWorkspace, type: "document" }} />);
    expect(screen.getByText("DOCUMENTATION & REPORT WORKSPACE")).toBeDefined();
    expect(screen.getByText("Test summary description")).toBeDefined();
    expect(screen.queryByText("0 critical regressions identified.")).toBeNull();
  });

  test("DocumentWorkspace renders supplied document and honest empty state", () => {
    const { rerender } = render(<DocumentWorkspace workspace={{ ...mockWorkspace, type: "document", summary: "" }} />);
    expect(screen.getByText("NO DOCUMENT SELECTED.")).toBeDefined();
    expect(screen.queryByText("Analysis Report")).toBeNull();

    rerender(<DocumentWorkspace workspace={{
      ...mockWorkspace,
      type: "document",
      summary: "",
      contentState: { markdown: "# Real Report\n\nAuthoritative body." },
    }} />);
    expect(screen.getByText("Real Report")).toBeDefined();
    expect(screen.getByText("Authoritative body.")).toBeDefined();
  });

  test("DocumentWorkspace treats malformed document content as unavailable", () => {
    render(<DocumentWorkspace workspace={{
      ...mockWorkspace,
      type: "document",
      summary: "",
      contentState: { markdown: { body: "not a document string" } },
    }} />);

    expect(screen.getByText("NO DOCUMENT SELECTED.")).toBeDefined();
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
    expect(screen.getByText("RESULT: RESULT AVAILABLE")).toBeDefined();
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
