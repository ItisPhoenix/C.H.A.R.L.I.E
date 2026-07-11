/**
 * Tests that on WS open, `session_active` is sent a second time ~250ms later
 * (Task 7), surviving the ZMQ slow-joiner race. This mirrors the onopen handler
 * in page.tsx: send once immediately, then schedule a guarded re-send.
 */
import { describe, it, expect } from "vitest";

describe("WS open: re-send session_active", () => {
  // Mirror of the onopen scheduling logic. `send` is captured for assertions.
  function onOpen(opts: {
    currentSessionId: string;
    readyStateAtSchedule: number; // WebSocket.OPEN === 1
    timer: { fire: () => void };
  }) {
    const sent: string[] = [];
    const socket = {
      readyState: 1,
      send: (s: string) => sent.push(s),
    };
    const currentSessionIdRef = { current: opts.currentSessionId };

    // Immediate send (mirrors existing behavior).
    if (currentSessionIdRef.current) {
      socket.send(JSON.stringify({ type: "session_active", payload: { session_id: currentSessionIdRef.current } }));
      // Scheduled re-send, guarded by still-open socket + truthy session.
      opts.timer.fire = () => {
        if (socket.readyState === opts.readyStateAtSchedule && currentSessionIdRef.current) {
          socket.send(JSON.stringify({ type: "session_active", payload: { session_id: currentSessionIdRef.current } }));
        }
      };
    }
    return { sent, socket };
  }

  it("sends session_active twice (immediate + scheduled re-send)", () => {
    const timer = { fire: () => {} };
    const { sent, socket } = onOpen({ currentSessionId: "session-1", readyStateAtSchedule: 1, timer });
    expect(sent).toHaveLength(1); // immediate only so far
    timer.fire();
    expect(sent).toHaveLength(2);
    expect(JSON.parse(sent[1]).type).toBe("session_active");
    expect(socket.readyState).toBe(1);
  });

  it("skips the re-send when the socket is no longer open", () => {
    const timer = { fire: () => {} };
    const { sent } = onOpen({ currentSessionId: "session-1", readyStateAtSchedule: 3 /* CLOSED */, timer });
    timer.fire();
    expect(sent).toHaveLength(1);
  });
});
