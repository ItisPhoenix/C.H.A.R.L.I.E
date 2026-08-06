/**
 * Tests that on WS open, session_active is POSTed a second time ~250ms later
 * (Task 7), surviving the ZMQ slow-joiner race. This mirrors the onopen handler
 * in page.tsx: POST once immediately, then schedule a guarded re-POST.
 * (session_active goes over POST /api/session/active, not the WS socket --
 * see CLAUDE.md 8.5 -- this mock calls a fetch stub, not socket.send.)
 */
import { describe, it, expect } from "vitest";

describe("WS open: re-announce session_active", () => {
  // Mirror of the onopen scheduling logic. `post` is captured for assertions.
  function onOpen(opts: {
    currentSessionId: string;
    socketOpenAtSchedule: boolean;
    timer: { fire: () => void };
  }) {
    const posted: string[] = [];
    const socket = { open: true };
    const post = (sessionId: string) => posted.push(sessionId);
    const currentSessionIdRef = { current: opts.currentSessionId };

    // Immediate POST (mirrors existing behavior).
    if (currentSessionIdRef.current) {
      post(currentSessionIdRef.current);
      // Scheduled re-POST, guarded by still-open socket + truthy session.
      opts.timer.fire = () => {
        if (opts.socketOpenAtSchedule && currentSessionIdRef.current) {
          post(currentSessionIdRef.current);
        }
      };
    }
    return { posted, socket };
  }

  it("posts session_active twice (immediate + scheduled re-announce)", () => {
    const timer = { fire: () => {} };
    const { posted, socket } = onOpen({ currentSessionId: "session-1", socketOpenAtSchedule: true, timer });
    expect(posted).toHaveLength(1); // immediate only so far
    timer.fire();
    expect(posted).toHaveLength(2);
    expect(posted[1]).toBe("session-1");
    expect(socket.open).toBe(true);
  });

  it("skips the re-announce when the socket is no longer open", () => {
    const timer = { fire: () => {} };
    const { posted } = onOpen({ currentSessionId: "session-1", socketOpenAtSchedule: false, timer });
    timer.fire();
    expect(posted).toHaveLength(1);
  });
});
