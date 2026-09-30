import { afterEach, describe, expect, it, vi } from "vitest";
import { loadScene, orbStateForEvent, parseRuntimeEvent, parseSnapshot, postCommand } from "./runtime";

afterEach(() => vi.restoreAllMocks());

describe("live runtime boundary", () => {
  it("maps canonical runtime events to truthful orb states", () => {
    expect(orbStateForEvent("transcript")).toBe("listening");
    expect(orbStateForEvent("thinking")).toBe("working");
    expect(orbStateForEvent("speaking_start")).toBe("composing");
    expect(orbStateForEvent("response_done")).toBe("breathing");
    expect(orbStateForEvent("unrelated_event")).toBeNull();
  });

  it("accepts a valid snapshot and rejects malformed content", () => {
    expect(parseSnapshot({ revision: 3, title: "System status", details: [{ label: "CPU", value: "18%" }] })?.details).toHaveLength(1);
    expect(parseSnapshot({ revision: -1, title: "invalid" })).toBeNull();
    expect(parseSnapshot({ revision: 1, title: "invalid", details: [{ label: "CPU", value: 18 }] })).toBeNull();
  });

  it("reads the live scene endpoint and reports unsupported payloads", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ revision: 1, title: "Ready" }), { status: 200 }));
    await expect(loadScene(fetcher)).resolves.toMatchObject({ title: "Ready" });
    fetcher.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    await expect(loadScene(fetcher)).rejects.toThrow("unsupported scene snapshot");
  });

  it("projects scene updates from SSE and ignores malformed events", () => {
    expect(parseRuntimeEvent({ type: "scene.updated", payload: { snapshot: { revision: 4, title: "Updated" }, text: "Hello there" } })?.snapshot?.title).toBe("Updated");
    expect(parseRuntimeEvent({ type: "transcript", payload: { text: "Hello there" } })?.text).toBe("Hello there");
    expect(parseRuntimeEvent({ type: "token", payload: { text: "world" } })?.text).toBe("world");
    expect(parseRuntimeEvent({ type: "audio_level", payload: { level: 1.4 } })?.level).toBe(1);
    expect(parseRuntimeEvent({ nope: true })).toBeNull();
  });

  it("only reports command acceptance when the server explicitly confirms it", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ accepted: true }), { status: 200 }));
    await expect(postCommand("Check status", fetcher)).resolves.toBe(true);
    expect(JSON.parse(fetcher.mock.calls[0][1]?.body as string)).toEqual({ type: "submit_text", text: "Check status" });
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    await expect(postCommand("Check status", fetcher)).resolves.toBe(false);
  });

  it("keeps gateway error details available to the chat surface", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "All connection attempts failed" }), { status: 400 }));
    await expect(postCommand("Say hello", fetcher)).rejects.toThrow("All connection attempts failed");
  });
});
