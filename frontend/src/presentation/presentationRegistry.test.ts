import { describe, it, expect } from "vitest";
import {
  PRESENTATION_CONTRACT,
  CONTRACT_VERSION,
  SURFACE_SCHEMA_VERSION,
  resolveWorkspaceType,
  resolveWidgetType,
  resolveOverlayType,
  hasWorkspace,
  hasWidget,
  hasOverlay,
  hasPrimitive,
  getWorkspaceDefinition,
  getWidgetDefinition,
  getOverlayDefinition,
  listWorkspaces,
  listWidgets,
  listOverlays,
  listSurfacePrimitives,
  listLayoutTypes,
  listDismissPolicies,
  listPreferredZones,
  listPresentationKinds,
  getCoreStates,
  getCorePositions,
  getCoreRules,
} from "./presentationRegistry";
import { SCHEMA_VERSION } from "../composer/surfaceSchema";

describe("Frontend PresentationRegistry", () => {
  it("loads the shared presentation contract and distinguishes contract vs surface schema version", () => {
    expect(PRESENTATION_CONTRACT.contract_version).toBe(CONTRACT_VERSION);
    expect(PRESENTATION_CONTRACT.surface_schema_version).toBe(SURFACE_SCHEMA_VERSION);
    expect(SCHEMA_VERSION).toBe(SURFACE_SCHEMA_VERSION);

    expect(listPresentationKinds()).toContain("workspace");
    expect(listPresentationKinds()).toContain("widget");
    expect(listPresentationKinds()).toContain("composed_surface");
    expect(listPresentationKinds()).toContain("caption");
    expect(listPresentationKinds()).toContain("notification");
    expect(listPresentationKinds()).toContain("attention");
    expect(listPresentationKinds()).toContain("silent");
  });

  describe("Workspace resolution and alias canonicalization", () => {
    const expectedWorkspaces = [
      "research",
      "briefing",
      "system",
      "tasks",
      "map",
      "vision",
      "document",
      "terminal",
      "conversation",
      "composed_surface",
    ];

    it("enumerates all canonical workspaces (excluding settings overlay)", () => {
      const canonical = listWorkspaces();
      expect(canonical).toEqual(expect.arrayContaining(expectedWorkspaces));
      expect(canonical).not.toContain("settings");
    });

    it("resolves canonical workspace types", () => {
      for (const ws of expectedWorkspaces) {
        expect(hasWorkspace(ws)).toBe(true);
        expect(resolveWorkspaceType(ws)).toBe(ws);
        const def = getWorkspaceDefinition(ws);
        expect(def).not.toBeNull();
        expect(def?.name).toBe(ws);
        expect(def?.implemented).toBe(true);
      }
    });

    it("resolves all workspace aliases to canonical names", () => {
      expect(resolveWorkspaceType("telemetry")).toBe("system");
      expect(resolveWorkspaceType("task")).toBe("tasks");
      expect(resolveWorkspaceType("plans")).toBe("tasks");
      expect(resolveWorkspaceType("spatial")).toBe("map");
      expect(resolveWorkspaceType("camera")).toBe("vision");
      expect(resolveWorkspaceType("report")).toBe("document");
      expect(resolveWorkspaceType("file")).toBe("document");
      expect(resolveWorkspaceType("chat")).toBe("conversation");
    });

    it("identifies spatial workspace flag correctly", () => {
      const mapDef = getWorkspaceDefinition("spatial");
      expect(mapDef?.spatial).toBe(true);
      const researchDef = getWorkspaceDefinition("research");
      expect(researchDef?.spatial).toBe(false);
    });

    it("returns null for unknown workspace names or overlays", () => {
      expect(resolveWorkspaceType("unknown_workspace")).toBeNull();
      expect(hasWorkspace("unknown_workspace")).toBe(false);
      expect(getWorkspaceDefinition("unknown_workspace")).toBeNull();

      expect(resolveWorkspaceType("settings")).toBeNull();
      expect(hasWorkspace("settings")).toBe(false);
    });
  });

  describe("Overlay resolution", () => {
    it("recognizes settings as an overlay modal", () => {
      expect(listOverlays()).toContain("settings");
      expect(hasOverlay("settings")).toBe(true);
      expect(resolveOverlayType("settings")).toBe("settings");

      const def = getOverlayDefinition("settings");
      expect(def).not.toBeNull();
      expect(def?.name).toBe("settings");
      expect(def?.renderer).toBe("SettingsModal");
      expect(def?.dismiss_policy).toBe("manual");
    });
  });

  describe("Widget resolution and alias canonicalization", () => {
    const expectedWidgets = [
      "system_metric",
      "composed_surface",
      "media_control",
      "file_viewer",
    ];

    it("enumerates all canonical widgets", () => {
      const canonical = listWidgets();
      expect(canonical).toEqual(expect.arrayContaining(expectedWidgets));
    });

    it("resolves canonical widget types with accurate renderer metadata", () => {
      for (const w of expectedWidgets) {
        expect(hasWidget(w)).toBe(true);
        expect(resolveWidgetType(w)).toBe(w);
        const def = getWidgetDefinition(w);
        expect(def).not.toBeNull();
        expect(def?.name).toBe(w);
        expect(def?.supports.drag).toBe(true);
        expect(def?.supports.resize).toBe(true);
        expect(def?.supports.pin).toBe(true);
      }

      // Check accurate renderer names
      expect(getWidgetDefinition("system_metric")?.renderer).toBe("SystemWidget");
      expect(getWidgetDefinition("composed_surface")?.renderer).toBe("SurfaceComposer");
      expect(getWidgetDefinition("media_control")?.implemented).toBe(false);
      expect(getWidgetDefinition("media_control")?.renderer).toBe("UnavailableWidget");
      expect(getWidgetDefinition("file_viewer")?.implemented).toBe(false);
      expect(getWidgetDefinition("file_viewer")?.renderer).toBe("UnavailableWidget");
    });

    it("resolves all widget aliases", () => {
      expect(resolveWidgetType("system")).toBe("system_metric");
      expect(resolveWidgetType("media")).toBe("media_control");
      expect(resolveWidgetType("file")).toBe("file_viewer");
    });

    it("returns null for unknown widget names", () => {
      expect(resolveWidgetType("random_widget")).toBeNull();
      expect(hasWidget("random_widget")).toBe(false);
      expect(getWidgetDefinition("random_widget")).toBeNull();
    });
  });

  describe("Surface primitives and layout types", () => {
    it("matches all 20 SurfaceComposer primitive types", () => {
      const primitives = listSurfacePrimitives();
      expect(primitives).toHaveLength(20);
      expect(hasPrimitive("heading")).toBe(true);
      expect(hasPrimitive("text")).toBe(true);
      expect(hasPrimitive("metric")).toBe(true);
      expect(hasPrimitive("progress")).toBe(true);
      expect(hasPrimitive("list")).toBe(true);
      expect(hasPrimitive("table")).toBe(true);
      expect(hasPrimitive("chart")).toBe(true);
      expect(hasPrimitive("timeline")).toBe(true);
      expect(hasPrimitive("image")).toBe(true);
      expect(hasPrimitive("source")).toBe(true);
      expect(hasPrimitive("status")).toBe(true);
      expect(hasPrimitive("badge")).toBe(true);
      expect(hasPrimitive("divider")).toBe(true);
      expect(hasPrimitive("action")).toBe(true);
      expect(hasPrimitive("layout")).toBe(true);
      expect(hasPrimitive("spatial_map")).toBe(true);
      expect(hasPrimitive("density_heatmap")).toBe(true);
      expect(hasPrimitive("telemetry_gauges")).toBe(true);
      expect(hasPrimitive("process_telemetry")).toBe(true);
      expect(hasPrimitive("map_placeholder")).toBe(true);
    });

    it("matches layout types", () => {
      const layouts = listLayoutTypes();
      expect(layouts).toEqual(
        expect.arrayContaining(["stack", "row", "grid", "columns", "section"])
      );
    });

    it("matches dismiss policies and placement zones", () => {
      const policies = listDismissPolicies();
      expect(policies).toEqual(
        expect.arrayContaining(["immediate", "timed", "manual", "persistent", "task_lifetime"])
      );

      const zones = listPreferredZones();
      expect(zones).toEqual(
        expect.arrayContaining(["contextual", "top_right", "bottom_right", "top_left", "bottom_left", "center"])
      );
    });
  });

  describe("Core states and positioning invariants", () => {
    it("contains required core states and positions", () => {
      const states = getCoreStates();
      expect(states).toContain("idle");
      expect(states).toContain("listening");
      expect(states).toContain("speaking");

      const positions = getCorePositions();
      expect(positions).toContain("center");
      expect(positions).toContain("dock_bottom_right");
    });

    it("models authoritative positioning rules", () => {
      const rules = getCoreRules();
      expect(rules.no_workspace.position).toBe("center");
      expect(rules.no_workspace.show_status_bar).toBe(true);
      expect(rules.active_workspace.position).toBe("dock_bottom_right");
      expect(rules.active_workspace.show_status_bar).toBe(false);
    });
  });
});
