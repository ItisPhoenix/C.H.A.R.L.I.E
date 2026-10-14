import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("voice-glow", () => ({ VoiceBeam: () => null }));
import { DashboardWidgetsPanel } from "./App";
import { parseRuntimeEvent, type DashboardWidgets } from "./runtime";

describe("dashboard widgets", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    localStorage.clear();
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  });

  it("positions the clock and timer as separate floating cards", () => {
    act(() => root.render(createElement(DashboardWidgetsPanel, {
      widgets: {
        clockVisible: true,
        timers: [
          { id: "timer-1", text: "Tea", dueAt: "2026-10-10T22:30:00Z" },
          { id: "timer-2", text: "Timer finished", dueAt: "2026-10-10T22:35:00Z" },
        ],
      },
      sessionId: "session-test",
      researching: false,
      onWidgetVisibilityChange: vi.fn(async () => undefined),
      onUpdateTimer: vi.fn(),
      onStopwatchAction: vi.fn(),
      onMediaCommand: vi.fn(),
    })));

    const clock = host.querySelector<HTMLElement>('[data-widget-id="clock"]');
    const firstTimer = host.querySelector<HTMLElement>('[data-widget-id="timer:timer-1"]');
    const secondTimer = host.querySelector<HTMLElement>('[data-widget-id="timer:timer-2"]');
    expect(clock).not.toBeNull();
    expect(firstTimer).not.toBeNull();
    expect(secondTimer).not.toBeNull();
    expect([firstTimer?.style.left, firstTimer?.style.top]).not.toEqual([secondTimer?.style.left, secondTimer?.style.top]);
    expect(clock?.style.position).toBe("absolute");
    expect(firstTimer?.style.position).toBe("absolute");
    expect(secondTimer?.style.position).toBe("absolute");
    expect(firstTimer?.querySelector('[aria-label="Close timer widget"]')).not.toBeNull();
    expect(secondTimer?.querySelector('[aria-label="Close timer widget"]')).not.toBeNull();
    expect(firstTimer?.querySelector('[aria-label="Edit timer Tea"]')).not.toBeNull();
    expect(secondTimer?.querySelector('[aria-label="Edit timer Timer finished"]')).not.toBeNull();
    expect(secondTimer?.textContent).toContain("Finishing in");
  });

  it("keeps voice delivery metadata when parsing the live widget event", () => {
    const event = parseRuntimeEvent({
      type: "dashboard_widgets",
      payload: {
        widgets: {
          clock_visible: true,
          hidden_widgets: ["music", "timer:voice-timer"],
          timers: [{ id: "voice-timer", text: "Tea", due_at: "2026-10-11T15:00:00Z", delivery_channel: "voice" }],
        },
      },
    });

    expect(event?.widgets?.timers[0]?.deliveryChannel).toBe("voice");
    expect(event?.widgets?.hiddenWidgets).toEqual(["music", "timer:voice-timer"]);
  });

  it("keeps a voice timer's alert channel when editing it from the dashboard card", async () => {
    const onUpdateTimer = vi.fn(async () => undefined);
    act(() => root.render(createElement(DashboardWidgetsPanel, {
      widgets: {
        clockVisible: false,
        timers: [{ id: "voice-timer", text: "Tea", dueAt: "2026-10-10T22:35:00Z", deliveryChannel: "voice" }],
      },
      sessionId: "session-test",
      researching: false,
      onWidgetVisibilityChange: vi.fn(async () => undefined),
      onUpdateTimer,
      onStopwatchAction: vi.fn(),
      onMediaCommand: vi.fn(),
    })));

    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Edit timer Tea"]')?.click());
    await act(async () => {
      host.querySelector("form")?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    expect(onUpdateTimer).toHaveBeenCalledWith("voice-timer", "Tea", 1, "voice");
  });

  it("exposes stopwatch controls from its edit action", () => {
    act(() => root.render(createElement(DashboardWidgetsPanel, {
      widgets: {
        clockVisible: true,
        timers: [],
        stopwatch: { state: "running", elapsedSeconds: 12, sampledAt: new Date().toISOString() },
      },
      sessionId: "session-test",
      researching: false,
      onWidgetVisibilityChange: vi.fn(async () => undefined),
      onUpdateTimer: vi.fn(),
      onStopwatchAction: vi.fn(),
      onMediaCommand: vi.fn(),
    })));

    const stopwatch = host.querySelector<HTMLElement>('[data-widget-id="stopwatch"]');
    expect(stopwatch?.querySelector('[aria-label="Edit stopwatch"]')).not.toBeNull();
    act(() => stopwatch?.querySelector<HTMLButtonElement>('[aria-label="Edit stopwatch"]')?.click());
    expect(stopwatch?.querySelector('[aria-label="Pause stopwatch"]')).not.toBeNull();
    expect(stopwatch?.querySelector('[aria-label="Reset stopwatch"]')).not.toBeNull();
  });

  it("shows the real now-playing card with native media controls", async () => {
    let resolveMediaCommand: (() => void) | undefined;
    const onMediaCommand = vi.fn(() => new Promise<void>((resolve) => { resolveMediaCommand = resolve; }));
    const onWidgetVisibilityChange = vi.fn(async () => undefined);
    const widgets = {
      clockVisible: true,
      timers: [],
      media: {
        title: "Current track",
        artist: "Current artist",
        artUri: "data:image/jpeg;base64,AA==",
        status: "playing",
        positionSeconds: 12,
        durationSeconds: 180,
        sampledAt: new Date().toISOString(),
      },
    } as unknown as DashboardWidgets;
    act(() => root.render(createElement(DashboardWidgetsPanel, {
      widgets,
      sessionId: "session-test",
      researching: false,
      onWidgetVisibilityChange,
      onUpdateTimer: vi.fn(),
      onStopwatchAction: vi.fn(),
      onMediaCommand,
    })));

    const music = host.querySelector<HTMLElement>('[data-widget-id="music"]');
    expect(music?.textContent).toContain("Current track");
    expect(music?.textContent).toContain("Current artist");
    expect(music?.querySelector("img")?.getAttribute("src")).toBe("data:image/jpeg;base64,AA==");
    expect(music?.querySelector('[aria-label="Close media widget"]')).not.toBeNull();
    await act(async () => {
      music?.querySelector<HTMLButtonElement>('[aria-label="Pause playback"]')?.click();
    });
    expect(onMediaCommand).toHaveBeenCalledWith("play/pause");
    expect(music?.querySelector<HTMLButtonElement>('[aria-label="Updating playback status"]')?.getAttribute("aria-busy")).toBe("true");
    expect(music?.querySelector('[aria-label="Resume playback"]')).toBeNull();
    await act(async () => { resolveMediaCommand?.(); });
    expect(music?.querySelector('[aria-label="Pause playback"]')).not.toBeNull();
    await act(async () => { music?.querySelector<HTMLButtonElement>('[aria-label="Next track"]')?.click(); });
    expect(onMediaCommand).toHaveBeenCalledWith("next track");
    act(() => music?.querySelector<HTMLButtonElement>('[aria-label="Close media widget"]')?.click());
    expect(host.querySelector('[data-widget-id="music"]')).toBeNull();
    expect(onWidgetVisibilityChange).toHaveBeenCalledWith("music", false, undefined);
  });

  it("uses runtime widget visibility so Charlie can hide and restore the music card", () => {
    const widgets = {
      clockVisible: true,
      timers: [],
      hiddenWidgets: ["music"],
      media: { available: true, adapterAvailable: true, title: "Track", artist: "Artist", status: "playing", positionSeconds: 0, durationSeconds: 0 },
    } as DashboardWidgets;
    const props = {
      sessionId: "session-test",
      researching: false,
      onWidgetVisibilityChange: vi.fn(async () => undefined),
      onUpdateTimer: vi.fn(async () => undefined),
      onStopwatchAction: vi.fn(async () => undefined),
      onMediaCommand: vi.fn(async () => undefined),
    };
    act(() => root.render(createElement(DashboardWidgetsPanel, { ...props, widgets })));
    expect(host.querySelector('[data-widget-id="music"]')).toBeNull();

    act(() => root.render(createElement(DashboardWidgetsPanel, { ...props, widgets: { ...widgets, hiddenWidgets: [] } })));
    expect(host.querySelector('[data-widget-id="music"]')).not.toBeNull();
  });

  it("reflows visible cards beside chat and disables drag repositioning until chat closes", () => {
    act(() => root.render(createElement(DashboardWidgetsPanel, {
      widgets: { clockVisible: true, hiddenWidgets: ["weather", "timer", "stopwatch", "music"], timers: [] },
      sessionId: "chat-open", chatOpen: true, researching: false,
      onWidgetVisibilityChange: vi.fn(async () => undefined),
      onUpdateTimer: vi.fn(async () => undefined),
      onStopwatchAction: vi.fn(async () => undefined),
      onMediaCommand: vi.fn(async () => undefined),
    })));
    const shelf = host.querySelector<HTMLElement>(".assistant-widgets");
    const clock = host.querySelector<HTMLElement>('[data-widget-id="clock"]');
    const drag = host.querySelector<HTMLElement>(".assistant-widget__drag");
    expect(shelf?.classList.contains("is-chat-open")).toBe(true);
    expect(clock?.style.left).toBe("");
    expect(clock?.style.top).toBe("");
    expect(drag?.getAttribute("aria-disabled")).toBe("true");
    expect(drag?.title).toContain("Close chat");
  });

  it("keeps multiple opted-in cards within one side shelf beside chat", () => {
    act(() => root.render(createElement(DashboardWidgetsPanel, {
      widgets: {
        clockVisible: true, hiddenWidgets: ["timer", "stopwatch", "music"], timers: [],
        weather: { location: "Kanpur", temperature: 29, temperatureUnit: "°C", weatherCode: 0, observedAt: "10:00" },
      },
      sessionId: "chat-open-two-cards", chatOpen: true, researching: false,
      onWidgetVisibilityChange: vi.fn(async () => undefined),
      onUpdateTimer: vi.fn(async () => undefined),
      onStopwatchAction: vi.fn(async () => undefined),
      onMediaCommand: vi.fn(async () => undefined),
    })));
    expect(host.querySelectorAll(".assistant-widgets.is-chat-open > .assistant-widget")).toHaveLength(2);
    expect(host.querySelector('[data-widget-id="clock"]')?.getAttribute("style")).toBeNull();
    expect(host.querySelector('[data-widget-id="weather"]')?.getAttribute("style")).toBeNull();
  });

  it("shows live system metrics on demand and hides the card through the shared visibility command", () => {
    const onWidgetVisibilityChange = vi.fn(async () => undefined);
    const props = {
      sessionId: "system-status-widget", chatOpen: true, researching: false,
      onWidgetVisibilityChange,
      onUpdateTimer: vi.fn(async () => undefined),
      onStopwatchAction: vi.fn(async () => undefined),
      onMediaCommand: vi.fn(async () => undefined),
    };
    const status = { cpuPercent: 18, memoryPercent: 42, gpuPercent: 11, diskPercent: 63, uptimeSeconds: 3600 };
    act(() => root.render(createElement(DashboardWidgetsPanel, {
      ...props, widgets: { clockVisible: false, hiddenWidgets: ["system_status"], timers: [], systemStatus: status },
    })));
    expect(host.querySelector('[data-widget-id="system_status"]')).toBeNull();

    act(() => root.render(createElement(DashboardWidgetsPanel, {
      ...props, widgets: { clockVisible: false, hiddenWidgets: [], timers: [], systemStatus: status },
    })));
    const card = host.querySelector<HTMLElement>('[data-widget-id="system_status"]');
    expect(card?.textContent).toContain("SYSTEM STATUS");
    expect(card?.textContent).toContain("CPU18%");
    expect(card?.textContent).toContain("RAM42%");
    expect(card?.textContent).toContain("GPU11%");
    expect(card?.textContent).toContain("Disk63%");
    expect(card?.style.left).toBe("");
    act(() => card?.querySelector<HTMLButtonElement>('[aria-label="Close system status widget"]')?.click());
    expect(onWidgetVisibilityChange).toHaveBeenCalledWith("system_status", false, undefined);
  });
});
