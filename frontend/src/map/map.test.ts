import { describe, it, expect, beforeEach } from "vitest";
import { useMapStore } from "./mapStore";
import { calculateFlyDuration, calculateBounds, CHARLIE_SAFE_PADDING } from "./camera";
import { resolveBasemapStyle } from "./providers/basemapProvider";
import { INTELLIGENCE_LAYERS, LAYER_BY_ID } from "./layers/registry";
import { CHARLIE_DARK_MAP_STYLE } from "./mapStyle";

describe("Map Subsystem Unit Tests", () => {
  beforeEach(() => {
    useMapStore.setState({
      longitude: 15.0,
      latitude: 25.0,
      zoom: 1.8,
      pitch: 0,
      bearing: 0,
      activeLayers: {},
      layerStatus: {},
      layerData: {},
      selectedFeature: null,
      route: null,
      pendingCommand: null,
      commandRevision: 0,
    });
  });

  it("enforces zero-intelligence layer default invariant", () => {
    // Invariant: every single intelligence layer definition must have defaultEnabled: false
    for (const layer of INTELLIGENCE_LAYERS) {
      expect(layer.defaultEnabled).toBe(false);
    }

    // Invariant: fresh store state must have 0 active layers
    const state = useMapStore.getState();
    expect(Object.keys(state.activeLayers).length).toBe(0);
  });

  it("handles command queue revision increments and consumption", () => {
    const store = useMapStore.getState();
    expect(store.commandRevision).toBe(0);

    store.dispatchCommand({ type: "zoom_in" });
    const stateAfterCmd1 = useMapStore.getState();
    expect(stateAfterCmd1.commandRevision).toBe(1);
    expect(stateAfterCmd1.pendingCommand?.revision).toBe(1);
    expect(stateAfterCmd1.pendingCommand?.command.type).toBe("zoom_in");

    // Consume revision 1
    store.consumeCommand(1);
    const stateAfterConsume = useMapStore.getState();
    expect(stateAfterConsume.pendingCommand).toBeNull();
  });

  it("calculates camera fly duration adaptively based on distance", () => {
    // Short hop (Tokyo Station to Shibuya ~5km)
    const shortDuration = calculateFlyDuration(139.7671, 35.6812, 139.7016, 35.6580);
    expect(shortDuration).toBe(450);

    // Sub-continental (Delhi to Mumbai)
    const midDuration = calculateFlyDuration(77.1025, 28.7041, 72.8777, 19.0760);
    expect(midDuration).toBe(750);

    // Global leap (Tokyo to San Francisco ~8000km)
    const longDuration = calculateFlyDuration(139.6917, 35.6895, -122.4194, 37.7749);
    expect(longDuration).toBe(1200);
  });

  it("calculates bounding box from route coordinate geometry", () => {
    const coords: [number, number][] = [
      [77.1025, 28.7041], // Delhi
      [76.5000, 27.8000],
      [75.7873, 26.9124], // Jaipur
    ];

    const bbox = calculateBounds(coords);
    expect(bbox).not.toBeNull();
    if (bbox) {
      expect(bbox[0][0]).toBeCloseTo(75.7873); // minLon
      expect(bbox[0][1]).toBeCloseTo(26.9124); // minLat
      expect(bbox[1][0]).toBeCloseTo(77.1025); // maxLon
      expect(bbox[1][1]).toBeCloseTo(28.7041); // maxLat
    }

    // Docked Charlie Core safe zone padding
    expect(CHARLIE_SAFE_PADDING.right).toBe(280);
    expect(CHARLIE_SAFE_PADDING.bottom).toBe(120);
  });

  it("resolves dark cyan basemap style in hybrid mode", () => {
    // With custom online URL
    const onlineStyle = resolveBasemapStyle({
      mode: "online",
      onlineSourceUrl: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
    });
    expect(onlineStyle).toBe("https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json");

    // With default Charlie dark vector style
    const defaultStyle = resolveBasemapStyle({
      mode: "hybrid",
      onlineSourceUrl: null,
      pmtilesUrl: null,
    });
    expect(defaultStyle).toEqual(CHARLIE_DARK_MAP_STYLE);
    expect((defaultStyle as typeof CHARLIE_DARK_MAP_STYLE).version).toBe(8);
  });

  it("verifies intelligence layer registry definitions", () => {
    expect(LAYER_BY_ID.has("earthquakes")).toBe(true);
    expect(LAYER_BY_ID.has("wildfires")).toBe(true);
    expect(LAYER_BY_ID.has("weather")).toBe(true);
    expect(LAYER_BY_ID.has("cyber_threats")).toBe(true);

    const eq = LAYER_BY_ID.get("earthquakes")!;
    expect(eq.category).toBe("Environment");
    expect(eq.attribution).toContain("USGS");
  });
});
