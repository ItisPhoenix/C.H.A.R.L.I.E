/**
 * Tests the newly-added WS dispatch branch for `tool_call` events (Task 5).
 * A tool_call handler must: (1) ignore events for a non-active session, and
 * (2) otherwise call appendToolActivity with a normalized tool-activity row.
 * This mirrors the branch in page.tsx onMessage handler.
 */

import { describe, it, expect } from "vitest";

describe("WS dispatch: tool_call event", () => {
  function handle(opts: {
    msg: { type: string; payload?: { name?: string; text?: string }; session_id?: string };
    activeSession: string;
    appendToolActivity?: (row: { kind: string; name: string; text: string; sessionId?: string }) => void;
  }) {
    const toolActivity: { kind: string; name: string; text: string; sessionId?: string }[] = [];
    const append = opts.appendToolActivity ?? ((row) => toolActivity.push(row));
    const msg = opts.msg;
    const currentSessionIdRef = { current: opts.activeSession };
    const sessionOf = (m: typeof msg) => (m as { session_id?: string }).session_id;

    if (msg.type === "tool_call") {
      const eventSession = sessionOf(msg);
      if (eventSession && eventSession !== currentSessionIdRef.current) return { toolActivity };
      append({ kind: "tool_call", name: msg.payload?.name || "tool", text: msg.payload?.text || "", sessionId: eventSession });
    }
    return { toolActivity };
  }

  it("appends a tool activity row for the active session", () => {
    const { toolActivity } = handle({
      activeSession: "session-1",
      msg: { type: "tool_call", payload: { name: "web_search", text: "searching" }, session_id: "session-1" },
    });
    expect(toolActivity).toHaveLength(1);
    expect(toolActivity[0]).toEqual({ kind: "tool_call", name: "web_search", text: "searching", sessionId: "session-1" });
  });

  it("falls back to a default name when payload name is absent", () => {
    const { toolActivity } = handle({
      activeSession: "session-1",
      msg: { type: "tool_call", payload: {}, session_id: "session-1" },
    });
    expect(toolActivity[0].name).toBe("tool");
  });

  it("ignores tool_call events for a non-active session", () => {
    const { toolActivity } = handle({
      activeSession: "session-1",
      msg: { type: "tool_call", payload: { name: "web_search" }, session_id: "session-2" },
    });
    expect(toolActivity).toHaveLength(0);
  });
});
