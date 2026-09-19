import { describe, expect, it } from "vitest";
import { getWidgetDefinition, inferWidgetId, isWidgetExpanded, isWidgetVisible, widgetRegistry } from "./widgets";

describe("widget registry", () => {
  it("defines one lifecycle contract for contextual widgets", () => {
    for (const widget of Object.values(widgetRegistry)) {
      expect(widget.id).toBeTruthy();
      expect(widget.source).toContain("runtime");
      expect(widget.preferredFootprint).toBeTruthy();
      expect(widget.preferredZone).toBeTruthy();
      expect(widget.supportedActions.length).toBeGreaterThan(0);
    }
    expect(getWidgetDefinition("conversation")?.autoShowTrigger).toBe("explicit");
    expect(getWidgetDefinition("tasks")?.autoShowTrigger).toBe("active-task");
    expect(getWidgetDefinition("system")?.immersiveWhenExpanded).toBe(false);
    expect(isWidgetVisible("compact")).toBe(true);
    expect(isWidgetVisible("dismissed")).toBe(false);
    expect(isWidgetExpanded("expanded")).toBe(true);
  });

  it("maps backend presentation identity to the canonical widget", () => {
    expect(inferWidgetId({ id: "conversation-workspace", workspace_type: "conversation" })).toBe("conversation");
    expect(inferWidgetId({ id: "research-workspace", kind: "workspace" })).toBe("research");
    expect(inferWidgetId({ id: "unknown", kind: "notification" })).toBeUndefined();
  });
});
