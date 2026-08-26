import { describe, expect, test, beforeEach } from "vitest";
import { useWidgetStore } from "./widgetStore";
import type { PresentationIntent } from "../store/charlie";
import type { ZoneContext } from "./zones";

beforeEach(() => {
  localStorage.clear();
  useWidgetStore.setState({
    widgets: {},
    topZIndex: 10,
    focusedWidgetId: null,
    pinnedLayouts: {},
  });
});

describe("WidgetManager Store & Lifecycle", () => {
  const dummyCtx: ZoneContext = {
    viewport: { width: 1920, height: 1080 },
    safeMargin: { x: 24, y: 24 },
    coreBounds: { x: 800, y: 400, width: 320, height: 320 },
    workspaceBounds: null,
  };

  const metricIntent: PresentationIntent = {
    id: "intent-cpu-1",
    kind: "widget",
    title: "CPU Telemetry",
    summary: "Load is 18%",
    widgetType: "system_metric",
    taskId: null,
    content: { cpu: 18 },
    priority: 30,
    attentionLevel: "normal",
    dismissPolicy: "timed",
    autoDismissMs: 5000,
    preferredZone: "top_right",
    anchor: "screen",
    createdAt: new Date().toISOString(),
    replayable: false,
    replaceKey: "widget:system_metric",
  };

  test("upsertWidget creates widget instance with collision-free position and auto-dismiss timer", () => {
    const widget = useWidgetStore.getState().upsertWidget(metricIntent, dummyCtx);

    expect(widget.id).toBe("intent-cpu-1");
    expect(widget.widgetType).toBe("system_metric");
    expect(widget.pinned).toBe(false);
    expect(widget.autoDismissMs).toBe(5000);
    expect(widget.expiresAt).toBeGreaterThan(Date.now());
    expect(useWidgetStore.getState().widgets["intent-cpu-1"]).toBeDefined();
  });

  test("canonicalizes registered widget aliases and preserves unknown types for explicit fallback", () => {
    const aliasWidget = useWidgetStore.getState().upsertWidget({ ...metricIntent, id: "alias-system", widgetType: "system" }, dummyCtx);
    const unknownWidget = useWidgetStore.getState().upsertWidget({ ...metricIntent, id: "unknown-widget", widgetType: "future_metric", replaceKey: "future_metric" }, dummyCtx);

    expect(aliasWidget.widgetType).toBe("system_metric");
    expect(unknownWidget.widgetType).toBe("future_metric");
  });

  test("replaceKey deduplication updates existing widget in place without spawning new cards", () => {
    useWidgetStore.getState().upsertWidget(metricIntent, dummyCtx);
    expect(Object.keys(useWidgetStore.getState().widgets).length).toBe(1);

    const updateIntent: PresentationIntent = {
      ...metricIntent,
      id: "intent-cpu-2",
      summary: "Load is 34%",
      content: { cpu: 34 },
    };

    useWidgetStore.getState().upsertWidget(updateIntent, dummyCtx);

    const widgets = Object.values(useWidgetStore.getState().widgets);
    expect(widgets.length).toBe(1);
    expect(widgets[0]?.id).toBe("intent-cpu-2");
    expect(widgets[0]?.summary).toBe("Load is 34%");
  });

  test("task progress widgets use stable replace_key to patch progress", () => {
    const taskIntent1: PresentationIntent = {
      id: "task-prog-1",
      kind: "widget",
      title: "File Indexing",
      summary: "Indexed 10/100 files",
      widgetType: "task_progress",
      taskId: "task-index-1",
      content: { progress: 10 },
      priority: 30,
      attentionLevel: "normal",
      dismissPolicy: "timed",
      autoDismissMs: null,
      preferredZone: "top_right",
      anchor: "screen",
      createdAt: new Date().toISOString(),
      replayable: false,
      replaceKey: "task:task-index-1",
    };

    useWidgetStore.getState().upsertWidget(taskIntent1, dummyCtx);

    const taskIntent2: PresentationIntent = {
      ...taskIntent1,
      id: "task-prog-2",
      summary: "Indexed 90/100 files",
      content: { progress: 90 },
    };

    useWidgetStore.getState().upsertWidget(taskIntent2, dummyCtx);

    const widgets = Object.values(useWidgetStore.getState().widgets);
    expect(widgets.length).toBe(1);
    expect(widgets[0]?.summary).toBe("Indexed 90/100 files");
  });

  test("pinning widget disables auto-dismiss and saves normalized layout to storage", () => {
    useWidgetStore.getState().upsertWidget(metricIntent, dummyCtx);
    useWidgetStore.getState().pinWidget("intent-cpu-1", dummyCtx.viewport);

    const pinned = useWidgetStore.getState().widgets["intent-cpu-1"];
    expect(pinned?.pinned).toBe(true);
    expect(pinned?.expiresAt).toBeNull();
    expect(pinned?.autoDismissMs).toBeNull();

    // Verify localStorage has saved normalized layout
    expect(useWidgetStore.getState().pinnedLayouts["widget:system_metric"]).toBeDefined();
  });

  test("unpin restores each widget registry TTL instead of a global constant", () => {
    const composedIntent: PresentationIntent = {
      ...metricIntent,
      id: "intent-composed-1",
      widgetType: "composed_surface",
      autoDismissMs: null,
      replaceKey: "widget:composed_surface",
    };

    useWidgetStore.getState().upsertWidget(metricIntent, dummyCtx);
    useWidgetStore.getState().upsertWidget(composedIntent, dummyCtx);
    useWidgetStore.getState().pinWidget("intent-cpu-1", dummyCtx.viewport);
    useWidgetStore.getState().pinWidget("intent-composed-1", dummyCtx.viewport);

    useWidgetStore.getState().unpinWidget("intent-cpu-1");
    useWidgetStore.getState().unpinWidget("intent-composed-1");

    expect(useWidgetStore.getState().widgets["intent-cpu-1"]?.autoDismissMs).toBe(5000);
    expect(useWidgetStore.getState().widgets["intent-composed-1"]?.autoDismissMs).toBe(8000);
    expect(useWidgetStore.getState().widgets["intent-cpu-1"]?.expiresAt).toBeGreaterThan(Date.now());
    expect(useWidgetStore.getState().widgets["intent-composed-1"]?.expiresAt).toBeGreaterThan(Date.now());
  });

  test("tickAutoDismiss dismisses expired temporary widgets but spares pinned and paused widgets", () => {
    const baseTime = 1000000;
    const expiredIntent: PresentationIntent = {
      ...metricIntent,
      id: "expired-1",
      replaceKey: "expired",
      autoDismissMs: 1000,
    };

    useWidgetStore.getState().upsertWidget(expiredIntent, dummyCtx);

    // Force expiresAt to baseTime + 1000
    useWidgetStore.setState((s) => ({
      widgets: {
        ...s.widgets,
        "expired-1": { ...s.widgets["expired-1"]!, expiresAt: baseTime + 1000 },
      },
    }));

    // Tick before expiration
    useWidgetStore.getState().tickAutoDismiss(baseTime + 500);
    expect(useWidgetStore.getState().widgets["expired-1"]).toBeDefined();

    // Tick after expiration
    useWidgetStore.getState().tickAutoDismiss(baseTime + 1500);
    expect(useWidgetStore.getState().widgets["expired-1"]).toBeUndefined();
  });

  test("pauseExpiry prevents expiration during user interaction", () => {
    const baseTime = 1000000;
    useWidgetStore.getState().upsertWidget(metricIntent, dummyCtx);

    useWidgetStore.setState((s) => ({
      widgets: {
        ...s.widgets,
        "intent-cpu-1": { ...s.widgets["intent-cpu-1"]!, expiresAt: baseTime + 1000 },
      },
    }));

    // Pause expiry (e.g. mouse hover)
    useWidgetStore.getState().pauseExpiry("intent-cpu-1");
    expect(useWidgetStore.getState().widgets["intent-cpu-1"]?.pausedExpiry).toBe(true);

    // Tick after expiration time
    useWidgetStore.getState().tickAutoDismiss(baseTime + 2000);
    // Should still exist because it's paused
    expect(useWidgetStore.getState().widgets["intent-cpu-1"]).toBeDefined();

    // Resume expiry
    useWidgetStore.getState().resumeExpiry("intent-cpu-1");
    expect(useWidgetStore.getState().widgets["intent-cpu-1"]?.pausedExpiry).toBe(false);
  });

  test("clearScreen removes temporary widgets while preserving pinned widgets", () => {
    useWidgetStore.getState().upsertWidget(metricIntent, dummyCtx);

    const pinnedIntent: PresentationIntent = {
      ...metricIntent,
      id: "intent-pinned-1",
      replaceKey: "pinned-key",
    };
    useWidgetStore.getState().upsertWidget(pinnedIntent, dummyCtx);
    useWidgetStore.getState().pinWidget("intent-pinned-1", dummyCtx.viewport);

    expect(Object.keys(useWidgetStore.getState().widgets).length).toBe(2);

    useWidgetStore.getState().clearScreen();

    const remaining = Object.values(useWidgetStore.getState().widgets);
    expect(remaining.length).toBe(1);
    expect(remaining[0]?.id).toBe("intent-pinned-1");
    expect(remaining[0]?.pinned).toBe(true);
  });

  test("clearEverything hides pinned widgets without unpinning, restoreEverything brings them back", () => {
    useWidgetStore.getState().upsertWidget(metricIntent, dummyCtx);
    useWidgetStore.getState().pinWidget("intent-cpu-1", dummyCtx.viewport);

    useWidgetStore.getState().clearEverything();
    expect(useWidgetStore.getState().widgets["intent-cpu-1"]?.minimized).toBe(true);
    expect(useWidgetStore.getState().widgets["intent-cpu-1"]?.pinned).toBe(true);

    useWidgetStore.getState().restoreEverything();
    expect(useWidgetStore.getState().widgets["intent-cpu-1"]?.minimized).toBe(false);
  });

  test("focusedEscape closes only currently focused widget without cascading", () => {
    useWidgetStore.getState().upsertWidget(metricIntent, dummyCtx);
    useWidgetStore.getState().focusWidget("intent-cpu-1");

    expect(useWidgetStore.getState().focusedWidgetId).toBe("intent-cpu-1");

    const handled = useWidgetStore.getState().focusedEscape();
    expect(handled).toBe(true);
    expect(useWidgetStore.getState().widgets["intent-cpu-1"]).toBeUndefined();
    expect(useWidgetStore.getState().focusedWidgetId).toBeNull();
  });
});
