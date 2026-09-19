import { beforeEach, describe, expect, it } from "vitest";
import { adaptEvent, resetEventDedupe } from "./bridge";

const canonical = (overrides: Record<string, unknown> = {}) => ({
  type: "research_progress",
  version: 1,
  id: "event-1",
  timestamp: "2026-09-17T10:00:00.000Z",
  source: "task",
  session_id: "session-1",
  task_id: "task-1",
  turn_id: "turn-1",
  replay: false,
  payload: { stage: "searching", message: "Searching", session_id: "session-1" },
  ...overrides,
});

beforeEach(() => resetEventDedupe());

describe("canonical runtime event bridge", () => {
  it("requires the shared envelope fields and preserves research identity", () => {
    const event = adaptEvent(canonical());
    expect(event).toMatchObject({
      type: "research_progress",
      version: 1,
      session_id: "session-1",
      task_id: "task-1",
      turn_id: "turn-1",
    });
    expect(adaptEvent({ ...canonical(), version: 2 })).toBeNull();
    expect(adaptEvent({ ...canonical(), id: "" })).toBeNull();
    expect(adaptEvent({ ...canonical(), timestamp: undefined })).toBeNull();
  });

  it("drops duplicate event IDs at the transport boundary", () => {
    const first = adaptEvent(canonical());
    const duplicate = adaptEvent(canonical());
    expect(first?.id).toBe("event-1");
    expect(duplicate).toBeNull();
  });

  it("does not invent unsupported research event types", () => {
    expect(adaptEvent(canonical({ type: "research_started" }))).toBeNull();
    expect(adaptEvent(canonical({ type: "research_source_update" }))).toBeNull();
  });
});
