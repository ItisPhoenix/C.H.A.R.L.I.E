import { describe, it, expect, beforeEach } from "vitest";
import { useMapStore } from "./mapStore";
import { calculateFlyDuration, calculateBounds, CHARLIE_SAFE_PADDING } from "./camera";
import { resolveBasemapStyle } from "./providers/basemapProvider";
import { INTELLIGENCE_LAYERS } from "./layers/registry";
import { CHARLIE_DARK_MAP_STYLE, createPMTilesStyle } from "./mapStyle";
import type { MapFeature, MapRoute } from "./types";

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
      layerMetadata: {},
      layerData: {},
      selectedFeature: null,
      route: null,
      pendingCommand: null,
      commandRevision: 0,
      userInteracting: false,
      lastUserInteractionTimestamp: 0,
      quality: "auto",
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

  it("handles all declared map commands in command queue", () => {
    const store = useMapStore.getState();

    // 1. fly_to
    store.dispatchCommand({ type: "fly_to", longitude: 139.69, latitude: 35.68, zoom: 12 });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("fly_to");

    // 2. ease_to
    store.dispatchCommand({ type: "ease_to", longitude: 77.10, latitude: 28.70, pitch: 45 });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("ease_to");

    // 3. fit_bounds
    store.dispatchCommand({ type: "fit_bounds", bounds: [[75.0, 26.0], [78.0, 29.0]] });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("fit_bounds");

    // 4. zoom_in
    store.dispatchCommand({ type: "zoom_in" });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("zoom_in");

    // 5. zoom_out
    store.dispatchCommand({ type: "zoom_out" });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("zoom_out");

    // 6. reset_north
    store.dispatchCommand({ type: "reset_north" });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("reset_north");

    // 7. set_pitch
    store.dispatchCommand({ type: "set_pitch", pitch: 45 });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("set_pitch");

    // 8. set_bearing
    store.dispatchCommand({ type: "set_bearing", bearing: 90 });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("set_bearing");

    // 9. focus_location
    store.dispatchCommand({ type: "focus_location", coordinates: [139.69, 35.68], zoom: 14 });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("focus_location");

    // 10. select_feature
    const sampleFeature: MapFeature = {
      id: "feat_1",
      label: "Test Point",
      category: "Sensor",
      coordinates: [139.69, 35.68],
    };
    store.dispatchCommand({ type: "select_feature", feature: sampleFeature });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("select_feature");

    // 11. set_layer
    store.dispatchCommand({ type: "set_layer", layerId: "earthquakes", enabled: true });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("set_layer");

    // 12. toggle_layer
    store.dispatchCommand({ type: "toggle_layer", layerId: "wildfires" });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("toggle_layer");

    // 13. set_route
    const sampleRoute: MapRoute = {
      start: [77.10, 28.70],
      startLabel: "Delhi",
      destination: [75.78, 26.91],
      destinationLabel: "Jaipur",
      geometry: [[77.10, 28.70], [75.78, 26.91]],
      distanceKm: 260,
    };
    store.dispatchCommand({ type: "set_route", route: sampleRoute, fit: true });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("set_route");

    // 14. clear_route
    store.dispatchCommand({ type: "clear_route" });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("clear_route");

    // 15. clear_selection
    store.dispatchCommand({ type: "clear_selection" });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("clear_selection");

    // 16. reset_map
    store.dispatchCommand({ type: "reset_map" });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("reset_map");
    expect(useMapStore.getState().commandRevision).toBe(16);
  });

  it("tracks camera user interaction state for interruption", () => {
    const store = useMapStore.getState();
    expect(store.userInteracting).toBe(false);

    store.recordUserInteraction();
    expect(useMapStore.getState().userInteracting).toBe(true);
    expect(useMapStore.getState().lastUserInteractionTimestamp).toBeGreaterThan(0);

    store.setUserInteracting(false);
    expect(useMapStore.getState().userInteracting).toBe(false);
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

  it("resolves dark cyan basemap style and handles PMTiles", () => {
    // With custom online URL
    const onlineStyle = resolveBasemapStyle({
      mode: "online",
      onlineSourceUrl: "https://tiles.openfreemap.org/planet",
    });
    expect(onlineStyle).toBe("https://tiles.openfreemap.org/planet");

    // With default Charlie dark vector style
    const defaultStyle = resolveBasemapStyle({
      mode: "hybrid",
      onlineSourceUrl: null,
      pmtilesUrl: null,
    });
    expect(defaultStyle).toBe(CHARLIE_DARK_MAP_STYLE);

    // With PMTiles URL
    const pmtilesStyle = createPMTilesStyle("/api/geo/pmtiles/sample.pmtiles");
    expect(pmtilesStyle.sources.local_pmtiles).toBeDefined();
  });

  it("manages intelligence layer lifecycle and metadata tracking", () => {
    const store = useMapStore.getState();

    // Enable layer
    store.setLayerEnabled("earthquakes", true);
    expect(useMapStore.getState().activeLayers.earthquakes).toBe(true);
    expect(useMapStore.getState().layerStatus.earthquakes.status).toBe("loading");

    // Store layer data with metadata
    const sampleFeatures: MapFeature[] = [
      {
        id: "eq_1",
        label: "M5.2 Earthquake",
        category: "Seismic",
        coordinates: [139.69, 35.68],
        severity: "medium",
      },
    ];
    store.setLayerData("earthquakes", sampleFeatures, {
      attribution: "USGS Seismic Feed",
      count: 1,
    });

    const state = useMapStore.getState();
    expect(state.layerData.earthquakes.length).toBe(1);
    expect(state.layerMetadata.earthquakes.attribution).toBe("USGS Seismic Feed");
    expect(state.layerMetadata.earthquakes.lastUpdated).toBeGreaterThan(0);
    expect(state.layerMetadata.earthquakes.count).toBe(1);

    // Disable layer
    store.setLayerEnabled("earthquakes", false);
    expect(useMapStore.getState().activeLayers.earthquakes).toBe(false);
    expect(useMapStore.getState().layerStatus.earthquakes.status).toBe("idle");
  });

  it("adapts quality tier in store", () => {
    const store = useMapStore.getState();
    expect(store.quality).toBe("auto");

    store.setQuality("low");
    expect(useMapStore.getState().quality).toBe("low");

    store.setQuality("high");
    expect(useMapStore.getState().quality).toBe("high");
  });
});
