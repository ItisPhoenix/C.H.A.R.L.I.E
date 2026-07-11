/**
 * Tests the HTTP /chat fallback guard (Task 8): when a session is in the
 * wsStreamingRef set (i.e. a WS reply is already streaming), the HTTP POST
 * fallback must NOT run. This mirrors the guard in page.tsx:handleSendMessage.
 */

import { describe, it, expect } from "vitest";

describe("HTTP /chat fallback guard", () => {
  // Mirror of the handleSendMessage fallback decision.
  function send(opts: {
    currentSessionId: string;
    socketOpen: boolean;
    wsStreamingRef: Set<string>;
    post: () => void;
  }) {
    const sid = opts.currentSessionId;
    if (!(opts.socketOpen) && !opts.wsStreamingRef.has(sid)) {
      opts.post();
    }
  }

  it("skips HTTP fallback when the session is streaming over WS", () => {
    const streaming = new Set<string>(["session-1"]);
    let posted = false;
    send({ currentSessionId: "session-1", socketOpen: false, wsStreamingRef: streaming, post: () => { posted = true; } });
    expect(posted).toBe(false);
  });

  it("runs HTTP fallback when socket is down and not streaming", () => {
    const streaming = new Set<string>(["other-session"]);
    let posted = false;
    send({ currentSessionId: "session-1", socketOpen: false, wsStreamingRef: streaming, post: () => { posted = true; } });
    expect(posted).toBe(true);
  });

  it("skips HTTP fallback when the socket is already open", () => {
    const streaming = new Set<string>();
    let posted = false;
    send({ currentSessionId: "session-1", socketOpen: true, wsStreamingRef: streaming, post: () => { posted = true; } });
    expect(posted).toBe(false);
  });
});
