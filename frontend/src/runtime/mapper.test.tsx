import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { mapRuntimeToScene } from "./mapper";
import { createInitialRuntimeState, runtimeReducer } from "./reducer";

const event = (type: string, payload: Record<string, unknown>) => ({ type, payload });
const actions = { approve: vi.fn(() => true), reject: vi.fn(() => true), dismiss: vi.fn(() => true) };

describe("runtime scene mapper", () => {
  it("maps live approval into the existing priority projection model", () => {
    let runtime = createInitialRuntimeState();
    runtime = runtimeReducer(runtime, { type: "connection", status: "connected" });
    runtime = runtimeReducer(runtime, { type: "event", event: event("tool_approval_request", {
      request_id: "approval-1", tool_name: "shell_execute", reason: "Run approved command", risk_class: "gated",
    }) });
    const scene = mapRuntimeToScene(runtime, actions);

    expect(scene.state).toBe("waiting-for-approval");
    expect(scene.items[0]).toMatchObject({
      id: "runtime-approval-approval-1",
      importance: 100,
      treatment: "hard",
      depth: "priority",
    });
    const view = render(<>{scene.items[0].content}</>);
    expect(view.getByRole("button", { name: "Approve" })).toBeTruthy();
    expect(view.getByRole("button", { name: "Reject" })).toBeTruthy();
  });

  it("keeps research content concise while exposing result identity", () => {
    let runtime = createInitialRuntimeState();
    runtime = runtimeReducer(runtime, { type: "event", event: event("research_result", {
      schema: "charlie.research_workspace", version: 1, query: "local route", mode: "standard",
      title: "Research & Synthesis", summary: "Result summary", status: "complete", confidence: 0.8,
      findings: [{ id: "F1", title: "Finding one", detail: "Evidence one", source_ids: ["S1"] }],
      sources: [{ id: "S1", title: "Source one", domain: "example.test", url: "https://example.test", snippet: "Evidence" }],
    }) });
    const scene = mapRuntimeToScene(runtime, actions);
    expect(scene.items.map(item => item.id)).toContain("runtime-research-result");
    expect(scene.items.find(item => item.id === "runtime-research-result")?.importance).toBe(98);
  });

  it("keeps progress peripheral until meaningful research content exists", () => {
    let runtime = createInitialRuntimeState();
    runtime = runtimeReducer(runtime, { type: "event", event: event("research_progress", {
      stage: "searching", message: "Searching official sources", session_id: "session-a",
    }) });
    const scene = mapRuntimeToScene(runtime, actions);
    const item = scene.items.find(value => value.id === "runtime-research-progress");
    expect(item?.immersive).toBe(false);
    expect(item?.relatedTo).toBe("charlie");
  });

  it("lets active research own the canvas instead of keeping a chat widget beside it", () => {
    let runtime = createInitialRuntimeState();
    runtime = runtimeReducer(runtime, { type: "event", event: event("chat", { text: "How's the weather?" }) });
    runtime = runtimeReducer(runtime, { type: "event", event: event("presentation_intent", {
      id: "conversation-workspace", kind: "workspace", workspace_type: "conversation",
    }) });
    runtime = runtimeReducer(runtime, { type: "event", event: event("research_progress", {
      stage: "reading", message: "Reading source 9/12", task_id: "research-task-1",
    },) });

    const scene = mapRuntimeToScene(runtime, actions);
    expect(scene.items.some(item => item.widgetId === "conversation")).toBe(false);
    expect(scene.items.some(item => item.id === "runtime-research-progress")).toBe(true);
  });

  it("does not project raw tool results as a standalone widget", () => {
    let runtime = createInitialRuntimeState();
    runtime = runtimeReducer(runtime, { type: "event", event: event("tool_result", {
      name: "desktop_close_app", text: '{"apps":["notepad"]}',
    }) });

    const scene = mapRuntimeToScene(runtime, actions);
    expect(scene.items.some(item => item.id === "runtime-tool-result")).toBe(false);
  });

  it("renders one canonical task widget and keeps compact content around Charlie", () => {
    let runtime = createInitialRuntimeState();
    runtime = runtimeReducer(runtime, { type: "connection", status: "connected" });
    runtime = runtimeReducer(runtime, { type: "event", event: event("background_task", {
      task: { id: "task-1", title: "Index notes", status: "running", current_action: "Reading" },
    }) });
    const scene = mapRuntimeToScene(runtime, actions);
    expect(scene.items.filter(item => item.widgetId === "tasks")).toHaveLength(1);
    expect(scene.items.some(item => item.id === "runtime-activity")).toBe(false);
    expect(scene.items.find(item => item.widgetId === "tasks")?.relatedTo).toBe("charlie");
  });

  it("translates degraded health and never projects raw telemetry", () => {
    let runtime = createInitialRuntimeState();
    runtime = runtimeReducer(runtime, { type: "connection", status: "connected" });
    runtime = runtimeReducer(runtime, { type: "event", event: event("runtime_truth", {
      status: "degraded", subsystems: { llm: { status: "unavailable", detail: "Primary LLM unavailable" } },
      authority: "main_runtime", timestamp: "hidden",
    }) });
    runtime = runtimeReducer(runtime, { type: "event", event: event("runtime_telemetry", {
      vitals: { authority: "main_runtime", timestamp: "hidden", cpu: 12 },
    }) });
    const quietScene = mapRuntimeToScene(runtime, actions);
    expect(quietScene.items.some(item => item.widgetId === "system")).toBe(false);
    runtime = runtimeReducer(runtime, { type: "event", event: event("presentation_intent", {
      id: "system-status", kind: "widget", title: "System health", workspace_type: "system",
    }) });
    const scene = mapRuntimeToScene(runtime, actions);
    const view = render(<>{scene.items.map(item => <div key={item.id}>{item.content}</div>)}</>);
    expect(view.getByText("LLM")).toBeTruthy();
    expect(view.queryByText(/authority|timestamp/i)).toBeNull();
  });
});
