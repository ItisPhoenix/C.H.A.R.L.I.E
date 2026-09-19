import { afterEach, describe, expect, it } from "vitest";
import { RuntimeClient } from "./client";

class FakeSocket {
  readyState = 0;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  sent: Record<string, unknown>[] = [];

  send(data: string) {
    this.sent.push(JSON.parse(data) as Record<string, unknown>);
  }

  close() {
    this.readyState = 3;
  }

  open() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }

  receive(message: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }
}

describe("RuntimeClient", () => {
  afterEach(() => sessionStorage.clear());

  it("uses canonical session, chat, and approval command envelopes", async () => {
    const socket = new FakeSocket();
    const events: Record<string, unknown>[] = [];
    const client = new RuntimeClient({
      url: "ws://localhost:8000/ws",
      reconnect: false,
      socketFactory: () => socket,
      fetchImpl: async () => ({ ok: true, json: async () => ({ session_id: "session-a" }) } as Response),
      onEvent: event => events.push(event),
    });

    client.connect();
    await new Promise(resolve => setTimeout(resolve, 0));
    socket.open();
    expect(socket.sent[0]).toMatchObject({ type: "session_active", payload: { session_id: "session-a" } });
    expect(client.sendChat("hello")).toBe(true);
    expect(socket.sent.at(-1)).toMatchObject({ type: "chat", payload: { session_id: "session-a", text: "hello" } });
    expect(events.at(-1)).toMatchObject({ type: "chat", payload: { text: "hello" } });

    expect(client.sendToolApproval("approval-1", true)).toBe(true);
    expect(client.sendToolApproval("approval-1", true)).toBe(false);
    expect(socket.sent.at(-1)).toMatchObject({ type: "tool_approve", payload: { request_id: "approval-1" } });
    socket.receive({
      type: "tool_approval_resolved", version: 1, id: "approval-resolved-1",
      timestamp: "2026-09-17T10:00:00.000Z", source: "runtime", session_id: "session-a",
      task_id: null, turn_id: null, replay: false, payload: { request_id: "approval-1" },
    });
    expect(events.at(-1)).toMatchObject({ type: "tool_approval_resolved" });
    expect(client.sendToolApproval("approval-1", false)).toBe(true);
    expect(client.ptt("start")).toBe(true);
    expect(socket.sent.at(-1)).toMatchObject({ type: "ptt_start", payload: {} });
    expect(client.openConversation()).toBe(true);
    expect(socket.sent.at(-1)).toMatchObject({ type: "presentation_command", payload: { action: "open_conversation" } });
  });

  it("creates a canonical web session when no active session is available", async () => {
    const socket = new FakeSocket();
    const requests: { url: string; init?: RequestInit }[] = [];
    const client = new RuntimeClient({
      url: "ws://localhost:8000/ws",
      reconnect: false,
      socketFactory: () => socket,
      fetchImpl: async (input, init) => {
        const url = String(input);
        requests.push({ url, init });
        if (url.endsWith("/api/session/active")) {
          return { ok: true, json: async () => ({ session_id: null }) } as Response;
        }
        return {
          ok: true,
          json: async () => ({ status: "completed", result: { session: { session_id: "session-created" } } }),
        } as Response;
      },
      onEvent: () => undefined,
    });

    client.connect();
    await new Promise(resolve => setTimeout(resolve, 0));
    socket.open();

    expect(requests.map(request => request.url)).toEqual([
      "http://localhost:8000/api/session/active",
      "http://localhost:8000/api/sessions",
    ]);
    expect(socket.sent[0]).toMatchObject({ type: "session_active", payload: { session_id: "session-created" } });
    expect(client.sendChat("hello after session creation")).toBe(true);
    expect(socket.sent.at(-1)).toMatchObject({
      type: "chat",
      payload: { session_id: "session-created", text: "hello after session creation" },
    });
  });

  it("blocks chat until the canonical session bootstrap completes", async () => {
    const socket = new FakeSocket();
    let releaseActive: (() => void) | undefined;
    const active = new Promise<void>(resolve => { releaseActive = resolve; });
    const client = new RuntimeClient({
      url: "ws://localhost:8000/ws",
      reconnect: false,
      socketFactory: () => socket,
      fetchImpl: async () => {
        await active;
        return { ok: true, json: async () => ({ session_id: "session-ready" }) } as Response;
      },
      onEvent: () => undefined,
    });

    client.connect();
    expect(client.sendChat("too early")).toBe(false);
    releaseActive?.();
    await new Promise(resolve => setTimeout(resolve, 0));
    socket.open();
    expect(client.sendChat("after bootstrap")).toBe(true);
  });

  it("rebinds an existing stored session without creating a parallel session", async () => {
    sessionStorage.setItem("charlie.runtime.session", "session-existing");
    const socket = new FakeSocket();
    const requests: { url: string; init?: RequestInit }[] = [];
    const client = new RuntimeClient({
      url: "ws://localhost:8000/ws",
      reconnect: false,
      socketFactory: () => socket,
      fetchImpl: async (input, init) => {
        const url = String(input);
        requests.push({ url, init });
        if (url.endsWith("/api/session/active") && init?.method !== "POST") {
          return { ok: true, json: async () => ({ session_id: null }) } as Response;
        }
        return { ok: true, json: async () => ({ status: "completed", result: { active_session_id: "session-existing" } }) } as Response;
      },
      onEvent: () => undefined,
    });

    client.connect();
    await new Promise(resolve => setTimeout(resolve, 0));
    socket.open();

    expect(requests.map(request => request.url)).toEqual([
      "http://localhost:8000/api/session/active",
      "http://localhost:8000/api/session/active",
    ]);
    expect(requests[1].init?.method).toBe("POST");
    expect(socket.sent[0]).toMatchObject({ type: "session_active", payload: { session_id: "session-existing" } });
    expect(requests.some(request => request.url.endsWith("/api/sessions"))).toBe(false);
  });

  it("replaces a stale stored session through canonical create authority", async () => {
    sessionStorage.setItem("charlie.runtime.session", "session-stale");
    const socket = new FakeSocket();
    const requests: { url: string; init?: RequestInit }[] = [];
    const client = new RuntimeClient({
      url: "ws://localhost:8000/ws",
      reconnect: false,
      socketFactory: () => socket,
      fetchImpl: async (input, init) => {
        const url = String(input);
        requests.push({ url, init });
        if (url.endsWith("/api/session/active") && init?.method !== "POST") {
          return { ok: true, json: async () => ({ session_id: null }) } as Response;
        }
        if (url.endsWith("/api/session/active")) {
          return { ok: true, json: async () => ({ status: "not_found", result: { failure_kind: "session_not_found" } }) } as Response;
        }
        return { ok: true, json: async () => ({ status: "completed", result: { session: { session_id: "session-recovered" } } }) } as Response;
      },
      onEvent: () => undefined,
    });

    client.connect();
    await new Promise(resolve => setTimeout(resolve, 0));
    socket.open();

    expect(requests.map(request => request.url)).toEqual([
      "http://localhost:8000/api/session/active",
      "http://localhost:8000/api/session/active",
      "http://localhost:8000/api/sessions",
    ]);
    expect(socket.sent[0]).toMatchObject({ type: "session_active", payload: { session_id: "session-recovered" } });
    expect(client.sendChat("after stale recovery")).toBe(true);
  });

  it("uses the authoritative media snapshot and control endpoints", async () => {
    const requests: { url: string; init?: RequestInit }[] = [];
    const fetchImpl: typeof fetch = async (input, init) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.endsWith("/api/media")) {
        return { ok: true, status: 200, json: async () => ({
          available: true, title: "Verified track", artist: "Verified artist", status: "playing",
          position_seconds: 10, duration_seconds: 120,
        }) } as Response;
      }
      return { ok: true, status: 200, json: async () => ({ status: "completed" }) } as Response;
    };
    const client = new RuntimeClient({
      url: "ws://localhost:8000/ws", reconnect: false, fetchImpl,
      onEvent: () => undefined,
    });
    const snapshot = await client.fetchMediaSnapshot();
    expect(snapshot.title).toBe("Verified track");
    expect(await client.controlMedia("play_pause")).toBe(true);
    expect(requests[1].init?.method).toBe("POST");
    expect(String(requests[1].init?.body)).toContain("play_pause");
  });
});
