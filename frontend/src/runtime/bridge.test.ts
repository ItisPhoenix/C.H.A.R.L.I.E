import { beforeEach, describe, expect, test } from "vitest";
import { adaptEvent, reconnectDelayMs, resetEventDedupe, shouldQueueCommand } from "./bridge";

beforeEach(() => {
  resetEventDedupe();
});

describe("reconnectDelayMs", () => {
  test("starts at 3000ms on the first attempt", () => {
    expect(reconnectDelayMs(0)).toBe(3000);
  });

  test("doubles each attempt", () => {
    expect(reconnectDelayMs(1)).toBe(6000);
    expect(reconnectDelayMs(2)).toBe(12000);
  });

  test("caps at 30000ms", () => {
    expect(reconnectDelayMs(10)).toBe(30000);
  });
});

describe("command queue safety", () => {
  test("does not queue process-global recovery decisions while disconnected", () => {
    expect(shouldQueueCommand("recovery_approve")).toBe(false);
    expect(shouldQueueCommand("recovery_reject")).toBe(false);
    expect(shouldQueueCommand("presentation_command")).toBe(true);
  });
});

describe("adaptEvent", () => {
  test("preserves a payload turn_id at the frontend transport boundary", () => {
    const event = adaptEvent({
      type: "token",
      id: "event-1",
      payload: { text: "hello", turn_id: "turn-1" },
    });

    expect(event?.turn_id).toBe("turn-1");
  });
});
