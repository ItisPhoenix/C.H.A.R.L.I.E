import { describe, expect, test } from "vitest";
import { adaptEvent, resetEventDedupe } from "./bridge";

describe("typed event contract adapter", () => {
  test("accepts a versioned event and preserves envelope metadata", () => {
    const event = adaptEvent({
      type: "charlie_state",
      version: 1,
      id: "evt-1",
      timestamp: "2026-08-15T00:00:00Z",
      source: "brain",
      session_id: "session-1",
      task_id: "task-1",
      replay: true,
      payload: { state: "idle" },
    });

    expect(event).toMatchObject({
      type: "charlie_state",
      id: "evt-1",
      source: "brain",
      session_id: "session-1",
      task_id: "task-1",
      replay: true,
    });
  });

  test("adapts legacy type-payload messages", () => {
    const event = adaptEvent({ type: "alert", payload: { message: "legacy" } });

    expect(event?.type).toBe("alert");
    expect(event?.version).toBe(1);
    expect(event?.replay).toBe(false);
    expect(event?.id).toBeTruthy();
  });

  test("rejects malformed payloads and unknown contract versions/types", () => {
    expect(adaptEvent({ type: "charlie_state", payload: "bad" })).toBeNull();
    expect(adaptEvent({ type: "charlie_state", version: 2, id: "x", timestamp: "now", payload: {} })).toBeNull();
    expect(adaptEvent({ type: "not_registered", payload: {} })).toBeNull();
  });

  test("dedupe boundary can be reset between reconnect sessions", () => {
    resetEventDedupe();
    const first = adaptEvent({ type: "alert", id: "evt-1", version: 1, timestamp: "now", payload: {} });
    const duplicate = adaptEvent({ type: "alert", id: "evt-1", version: 1, timestamp: "now", payload: {} });

    expect(first).not.toBeNull();
    expect(duplicate).toBeNull();

    resetEventDedupe();
    expect(adaptEvent({ type: "alert", id: "evt-1", version: 1, timestamp: "now", payload: {} })).not.toBeNull();
  });
});
