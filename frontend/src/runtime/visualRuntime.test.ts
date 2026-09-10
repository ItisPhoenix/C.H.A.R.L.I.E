import { describe, expect, test } from "vitest";
import {
  INITIAL_VISUAL_RUNTIME,
  reduceVisualRuntime,
  setVisualRuntimeConnection,
} from "./visualRuntime";

const event = (type: string, payload: Record<string, unknown>, timestamp: string, ids = {}) => ({
  type,
  payload,
  timestamp,
  ...ids,
});

describe("visual runtime projection", () => {
  test("projects real transcript and tool lifecycle with correlation", () => {
    const transcript = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("transcript", { text: "Open the terminal", session_id: "s1", turn_id: "t1" }, "2026-01-01T00:00:01.000Z", { session_id: "s1", turn_id: "t1" }),
    );
    expect(transcript).toMatchObject({ phase: "transcribing", transcript: "Open the terminal" });
    expect(transcript.correlation).toMatchObject({ sessionId: "s1", turnId: "t1" });

    const acting = reduceVisualRuntime(
      transcript,
      event("tool_call", { name: "desktop_open_app", session_id: "s1", turn_id: "t1", task_id: "task-1" }, "2026-01-01T00:00:02.000Z", { session_id: "s1", turn_id: "t1", task_id: "task-1" }),
    );
    expect(acting).toMatchObject({ phase: "acting", toolName: "desktop_open_app" });

    const complete = reduceVisualRuntime(
      acting,
      event("tool_result", { name: "desktop_open_app", text: "Opened", success: true }, "2026-01-01T00:00:03.000Z", { session_id: "s1", turn_id: "t1", task_id: "task-1" }),
    );
    expect(complete).toMatchObject({ phase: "success", label: "APPLICATION COMPLETE", detail: "Opened" });
  });

  test("rejects stale and mismatched completion events", () => {
    const acting = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("tool_call", { name: "browser", session_id: "s1", turn_id: "t1" }, "2026-01-01T00:00:02.000Z", { session_id: "s1", turn_id: "t1" }),
    );
    const mismatched = reduceVisualRuntime(
      acting,
      event("tool_result", { name: "browser", text: "old session" }, "2026-01-01T00:00:03.000Z", { session_id: "s2", turn_id: "t2" }),
    );
    expect(mismatched).toBe(acting);

    const stale = reduceVisualRuntime(
      acting,
      event("tool_call", { name: "older" }, "2026-01-01T00:00:01.000Z"),
    );
    expect(stale).toBe(acting);
  });

  test("never projects hidden reasoning text", () => {
    const projected = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("thinking_update", { text: "<think>private chain</think>" }, "2026-01-01T00:00:01.000Z"),
    );
    expect(projected).toBe(INITIAL_VISUAL_RUNTIME);
  });

  test("does not project internal thinking text or raw structured results", () => {
    const thinking = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("thinking_update", { text: "I'll use the tool with {password: secret}" }, "2099-01-01T00:00:01.000Z"),
    );
    expect(thinking).toBe(INITIAL_VISUAL_RUNTIME);

    const result = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("tool_result", { name: "desktop_open_app", text: "{\"password\":\"secret\"}", success: true }, "2099-01-01T00:00:02.000Z"),
    );
    expect(result.detail).toBeNull();

    const secretResult = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("tool_result", { name: "desktop_open_app", display_text: "prefix {\"api_key\":\"SECRET\"}", success: true }, "2099-01-01T00:00:03.000Z"),
    );
    expect(secretResult.detail).toBeNull();
  });

  test("does not claim success when the backend result has no authoritative status", () => {
    const result = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("tool_result", { name: "desktop_open_app", text: "Opened" }, "2099-01-01T00:00:04.000Z"),
    );
    expect(result).toMatchObject({ phase: "acting", label: "TOOL RESULT", detail: "Opened" });

    const failed = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("tool_result", { name: "desktop_open_app", text: "Error: launch failed" }, "2099-01-01T00:00:05.000Z"),
    );
    expect(failed).toMatchObject({ phase: "error", label: "APPLICATION FAILED" });
  });

  test("rejects a completion for another tool in the same correlation", () => {
    const acting = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("tool_call", { name: "browser_navigate" }, "2099-01-01T00:00:06.000Z", { session_id: "s1", turn_id: "t1", task_id: "task-1" }),
    );
    const stale = reduceVisualRuntime(
      acting,
      event("tool_result", { name: "desktop_open_app", success: true }, "2099-01-01T00:00:07.000Z", { session_id: "s1", turn_id: "t1", task_id: "task-1" }),
    );
    expect(stale).toBe(acting);
  });

  test("rejects a late tool result after a generic completed state", () => {
    const completed = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("charlie_state", { state: "completed" }, "2099-01-01T00:00:12.000Z", { session_id: "s1", turn_id: "t1" }),
    );
    expect(reduceVisualRuntime(
      completed,
      event("tool_result", { name: "desktop_open_app", success: true }, "2099-01-01T00:00:13.000Z", { session_id: "s1", turn_id: "t1" }),
    )).toBe(completed);
  });

  test("keeps recovery proposals actionable without claiming completion", () => {
    const recovering = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("recovery_proposal", {
        proposal_id: "proposal-1",
        explanation: "A safe local recovery is available.",
        proposed_command: "never show this",
      }, "2099-01-01T00:00:03.000Z"),
    );
    expect(recovering).toMatchObject({
      phase: "recovering",
      label: "RECOVERY AVAILABLE",
      detail: "A safe local recovery is available.",
      recoveryProposalId: "proposal-1",
    });
    expect(recovering.detail).not.toContain("never show this");
  });

  test("connection lifecycle clears stale activity", () => {
    const active = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("tool_call", { name: "research", session_id: "s1" }, "2026-01-01T00:00:01.000Z", { session_id: "s1" }),
    );
    const offline = setVisualRuntimeConnection(active, false);
    expect(offline).toMatchObject({ phase: "offline", label: "OFFLINE", detail: "Runtime connection unavailable" });
    expect(offline.correlation).toEqual({ sessionId: null, turnId: null, taskId: null, requestId: null });
    expect(setVisualRuntimeConnection(offline, true).phase).toBe("idle");
  });

  test("offline and reconnect require fresh correlated activity", () => {
    const active = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("tool_call", { name: "browser_navigate" }, "2099-01-01T00:00:08.000Z", { session_id: "s1", turn_id: "t1" }),
    );
    const offline = setVisualRuntimeConnection(active, false);
    expect(offline.requiresCorrelation).toBe(true);
    expect(reduceVisualRuntime(offline, event("tool_call", { name: "stale" }, "2099-01-01T00:00:09.000Z"))).toBe(offline);

    const reconnected = setVisualRuntimeConnection(offline, true);
    expect(reduceVisualRuntime(reconnected, event("tool_call", { name: "uncorrelated" }, "2099-01-01T00:00:10.000Z"))).toBe(reconnected);
    expect(reduceVisualRuntime(reconnected, event("tool_call", { name: "fresh" }, "2099-01-01T00:00:11.000Z", { session_id: "s1", turn_id: "t2" })).phase).toBe("acting");
    expect(reduceVisualRuntime(reconnected, event("charlie_state", { state: "completed" }, "2099-01-01T00:00:11.500Z"))).toBe(reconnected);
  });

  test("does not let unrelated completion or ambient health clear active work", () => {
    const acting = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("tool_call", { name: "desktop_open_app" }, "2026-01-01T00:00:01.000Z", { session_id: "s1", turn_id: "t1" }),
    );
    expect(reduceVisualRuntime(
      acting,
      event("speaking_stop", {}, "2026-01-01T00:00:02.000Z", { session_id: "s1", turn_id: "t1" }),
    )).toBe(acting);
    expect(reduceVisualRuntime(
      acting,
      event("speaking_stop", {}, "2026-01-01T00:00:02.000Z"),
    )).toBe(acting);
    expect(reduceVisualRuntime(
      acting,
      event("subsystem_health", { brain: { status: "degraded" } }, "2026-01-01T00:00:02.000Z"),
    )).toBe(acting);
  });

  test("resets child correlation when a new turn or session begins", () => {
    const acting = reduceVisualRuntime(
      INITIAL_VISUAL_RUNTIME,
      event("tool_call", { name: "browser", request_id: "r1" }, "2026-01-01T00:00:01.000Z", {
        session_id: "s1",
        turn_id: "t1",
        task_id: "task-1",
      }),
    );
    const nextTurn = reduceVisualRuntime(
      acting,
      event("transcript", { text: "Next" }, "2026-01-01T00:00:02.000Z", { session_id: "s1", turn_id: "t2" }),
    );
    expect(nextTurn.correlation).toEqual({ sessionId: "s1", turnId: "t2", taskId: null, requestId: null });
  });
});
