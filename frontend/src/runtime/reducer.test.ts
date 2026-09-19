import { describe, expect, it } from "vitest";
import { createInitialRuntimeState, runtimeReducer } from "./reducer";

let sequence = 0;
const event = (type: string, payload: Record<string, unknown>, identity: Record<string, unknown> = {}) => ({
  type,
  version: 1,
  id: `test-event-${++sequence}`,
  timestamp: "2026-09-17T10:00:00.000Z",
  source: "test",
  session_id: null,
  task_id: null,
  turn_id: null,
  replay: false,
  ...identity,
  payload,
});

describe("runtimeReducer", () => {
  it("keeps session-scoped streams isolated and finalizes one response", () => {
    let state = createInitialRuntimeState();
    state = runtimeReducer(state, { type: "session", sessionId: "session-a" });
    state = runtimeReducer(state, { type: "event", event: event("token", {
      session_id: "session-b", text: "private",
    }) });
    expect(state.conversation.responseText).toBe("");

    state = runtimeReducer(state, { type: "event", event: event("transcript", {
      session_id: "session-a", text: "status update", turn_id: "turn-1",
    }) });
    state = runtimeReducer(state, { type: "event", event: event("token", {
      session_id: "session-a", text: "Ready", turn_id: "turn-1",
    }) });
    state = runtimeReducer(state, { type: "event", event: event("token", {
      session_id: "session-a", text: ".", turn_id: "turn-1",
    }) });

    expect(state.conversation.responseText).toBe("Ready.");
    expect(state.conversation.streaming).toBe(true);
    state = runtimeReducer(state, { type: "event", event: event("response_done", {
      session_id: "session-a",
    }) });
    expect(state.conversation.streaming).toBe(false);
    expect(state.coreState).toBe("idle");
  });

  it("deduplicates approvals and removes them only after resolution", () => {
    let state = createInitialRuntimeState();
    const request = event("tool_approval_request", {
      request_id: "approval-1", tool_name: "shell_execute", reason: "Run command",
    });
    state = runtimeReducer(state, { type: "event", event: request });
    state = runtimeReducer(state, { type: "event", event: request });
    expect(Object.keys(state.approvals)).toEqual(["approval-1"]);
    expect(state.coreState).toBe("waiting-for-approval");

    state = runtimeReducer(state, { type: "approval_command", requestId: "approval-1" });
    const beforeDuplicate = state;
    state = runtimeReducer(state, { type: "approval_command", requestId: "approval-1" });
    expect(state).toBe(beforeDuplicate);
    expect(state.inFlightApprovals["approval-1"]).toBe(true);

    state = runtimeReducer(state, { type: "event", event: event("tool_approval_resolved", {
      request_id: "approval-1",
    }) });
    expect(state.approvals).toEqual({});
    expect(state.inFlightApprovals).toEqual({});
  });

  it("ignores unknown events without mutating runtime state", () => {
    const state = createInitialRuntimeState();
    const next = runtimeReducer(state, { type: "event", event: event("future_event", { value: 1 }) });
    expect(next).toBe(state);
  });

  it("keeps conversation hidden until a presentation intent summons it", () => {
    let state = createInitialRuntimeState();
    state = runtimeReducer(state, { type: "event", event: event("chat", { text: "Hello" }) });
    expect(state.conversation.userText).toBe("Hello");
    expect(state.widgets.conversation).toBeUndefined();
    state = runtimeReducer(state, { type: "event", event: event("presentation_intent", {
      id: "conversation-workspace", kind: "workspace", workspace_type: "conversation",
    }) });
    expect(state.widgets.conversation?.lifecycle).toBe("summoned");
    state = runtimeReducer(state, { type: "event", event: event("presentation_dismiss", {
      id: "conversation-workspace",
    }) });
    expect(state.widgets.conversation?.lifecycle).toBe("dismissed");
  });

  it("does not project task-owned tool progress into the conversation", () => {
    let state = createInitialRuntimeState();
    state = runtimeReducer(state, { type: "event", event: event("research_progress", {
      stage: "reading", message: "Reading source 9/12", current: 9, total: 12,
    }, { task_id: "research-task-1" }) });
    state = runtimeReducer(state, { type: "event", event: event("thinking_update", {
      text: "I'll use the research tool with {'stage': 'reading'}",
    }, { task_id: "research-task-1" }) });

    expect(state.thinking).toBe("");
    expect(state.research.progress?.message).toBe("Reading source 9/12");
    expect(state.coreState).toBe("acting");
  });

  it("reopens research when a new run starts after an older result was dismissed", () => {
    let state = createInitialRuntimeState();
    state = runtimeReducer(state, { type: "event", event: event("research_result", {
      schema: "charlie.research_workspace", version: 1, query: "old", mode: "standard",
      summary: "Old result", status: "complete", confidence: 1, findings: [], sources: [],
    }, { task_id: "research-task-old" }) });
    state = runtimeReducer(state, { type: "event", event: event("presentation_intent", {
      id: "research-workspace", kind: "workspace", workspace_type: "research",
    }) });
    state = runtimeReducer(state, { type: "event", event: event("presentation_dismiss", {
      id: "research-workspace",
    }) });
    state = runtimeReducer(state, { type: "event", event: event("research_progress", {
      stage: "searching", message: "Searching new sources", task_id: "research-task-new",
    }, { task_id: "research-task-new" }) });

    expect(state.widgets.research?.lifecycle).toBe("compact");
  });

  it("auto-summons one task widget for active task state", () => {
    let state = runtimeReducer(createInitialRuntimeState(), { type: "event", event: event("background_task", {
      task: { id: "task-1", title: "Index notes", status: "running" },
    }) });
    expect(state.widgets.tasks?.lifecycle).toBe("compact");
    expect(state.activeActivity?.kind).toBe("task");
    state = runtimeReducer(state, { type: "event", event: event("background_task", {
      task: { id: "task-1", title: "Index notes", status: "completed" },
    }) });
    expect(state.widgets.tasks?.lifecycle).toBe("collapsed");
  });

  it("ignores duplicate and stale research progress while keeping the newest stage", () => {
    let state = createInitialRuntimeState();
    state = runtimeReducer(state, { type: "event", event: event("research_progress", {
      stage: "searching", message: "Searching", session_id: "session-a", turn_id: "turn-a",
    }, { id: "research-2", timestamp: "2026-09-17T10:00:02.000Z", session_id: "session-a", turn_id: "turn-a" }) });
    const duplicate = state;
    state = runtimeReducer(state, { type: "event", event: event("research_progress", {
      stage: "searching", message: "Duplicate", session_id: "session-a", turn_id: "turn-a",
    }, { id: "research-2", timestamp: "2026-09-17T10:00:02.000Z", session_id: "session-a", turn_id: "turn-a" }) });
    expect(state).toBe(duplicate);
    state = runtimeReducer(state, { type: "event", event: event("research_progress", {
      stage: "planning", message: "Old planning event", session_id: "session-a", turn_id: "turn-a",
    }, { id: "research-1", timestamp: "2026-09-17T10:00:01.000Z", session_id: "session-a", turn_id: "turn-a" }) });
    expect(state.research.progress?.stage).toBe("searching");
  });

  it("does not hydrate a replayed Research presentation from another session", () => {
    let state = createInitialRuntimeState();
    state = runtimeReducer(state, { type: "session", sessionId: "session-current" });
    const oldPresentation = event("presentation_intent", {
      id: "presentation:workspace:research",
      kind: "workspace",
      workspace_type: "research",
      content: {
        schema: "charlie.research_workspace", version: 1, query: "old", mode: "standard",
        summary: "old", status: "complete", confidence: 1, findings: [], sources: [],
      },
      session_id: "session-old",
    }, { id: "old-presentation", session_id: "session-old", replay: true });
    state = runtimeReducer(state, { type: "event", event: oldPresentation });
    expect(state.research.result).toBeNull();
    expect(state.presentations).toEqual({});
  });
});
