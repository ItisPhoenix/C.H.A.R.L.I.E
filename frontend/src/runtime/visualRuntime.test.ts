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
    expect(complete).toMatchObject({ phase: "success", label: "TOOL COMPLETE", detail: "Opened" });
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
