/**
 * Tests the stale-session guard that fetchMessages must apply: when the active
 * session changes while a request is in flight, the resolved payload must NOT
 * overwrite the new session's thread. This mirrors the closure logic in
 * page.tsx:fetchMessages (capture `requestedSid`, then guard `if
 * (currentSessionIdRef.current !== requestedSid) return;` at resolve + finally).
 */
import { describe, it, expect } from "vitest";

describe("fetchMessages stale-session race guard", () => {
  // Mirror of the guard logic extracted from page.tsx.
  function applyResolve(opts: {
    requestedSid: string;
    activeSession: string;
    onApply: () => void;
    onFinally: () => void;
  }) {
    if (opts.activeSession !== opts.requestedSid) return; // stale: discard
    opts.onApply();
    if (opts.activeSession !== opts.requestedSid) return; // stale: skip finally side effects
    opts.onFinally();
  }

  it("discards the resolved payload when the active session changed", () => {
    const requestedSid = "old-session";
    let applied = false;
    let finallyRan = false;

    applyResolve({
      requestedSid,
      activeSession: "new-session",
      onApply: () => {
        applied = true;
      },
      onFinally: () => {
        finallyRan = true;
      },
    });

    expect(applied).toBe(false);
    expect(finallyRan).toBe(false);
  });

  it("applies the resolved payload when the active session is unchanged", () => {
    const requestedSid = "session-1";
    let applied = false;
    let finallyRan = false;

    applyResolve({
      requestedSid,
      activeSession: requestedSid,
      onApply: () => {
        applied = true;
      },
      onFinally: () => {
        finallyRan = true;
      },
    });

    expect(applied).toBe(true);
    expect(finallyRan).toBe(true);
  });
});
