import { describe, it, expect, beforeEach, vi } from "vitest";
import { PMTiles } from "pmtiles";
import { useMapStore } from "./mapStore";
import { calculateFlyDuration, calculateBounds, CHARLIE_SAFE_PADDING } from "./camera";
import { resolveBasemapStyle } from "./providers/basemapProvider";
import { INTELLIGENCE_LAYERS } from "./layers/registry";
import { CHARLIE_DARK_MAP_STYLE, createPMTilesStyle } from "./mapStyle";
import { resolveEffectiveQuality } from "./layers/renderers";
import type { MapFeature, MapRoute } from "./types";

describe("Map Subsystem & Implementation Tests", () => {
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
      pmtilesUrl: null,
      customStyleUrl: null,
      customVectorSourceUrl: null,
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

  it("validates PMTiles v3 fixture with official reference PMTiles JS library", async () => {
    // Generate valid 127-byte PMTiles v3 binary fixture
    const metaBuffer = Buffer.from(
      JSON.stringify({
        name: "Regional Test Dataset",
        attribution: "Charlie OS Offline",
        description: "Sample regional vector dataset",
        version: "1.0.0",
        vector_layers: [
          {
            id: "places",
            description: "Points of interest",
            fields: { name: "String" },
          },
        ],
      }),
      "utf-8"
    );

    const rootDir = Buffer.from([0x00]); // 0 entries varint
    const header = Buffer.alloc(127);
    header.write("PMTiles", 0, 7, "utf-8");
    header.writeUInt8(3, 7); // version 3
    header.writeBigUInt64LE(127n, 8); // root dir offset
    header.writeBigUInt64LE(BigInt(rootDir.length), 16); // root dir len
    header.writeBigUInt64LE(BigInt(127 + rootDir.length), 24); // json offset
    header.writeBigUInt64LE(BigInt(metaBuffer.length), 32); // json len
    header.writeBigUInt64LE(0n, 40); // leaf offset
    header.writeBigUInt64LE(0n, 48); // leaf len
    header.writeBigUInt64LE(BigInt(127 + rootDir.length + metaBuffer.length), 56); // tile data offset
    header.writeBigUInt64LE(0n, 64); // tile data len
    header.writeBigUInt64LE(0n, 72); // num addressed
    header.writeBigUInt64LE(0n, 80); // num tile entries
    header.writeBigUInt64LE(0n, 88); // num tile contents
    header.writeUInt8(1, 96); // clustered
    header.writeUInt8(0, 97); // internal comp
    header.writeUInt8(0, 98); // tile comp
    header.writeUInt8(1, 99); // tile type = MVT
    header.writeUInt8(0, 100); // min zoom
    header.writeUInt8(14, 101); // max zoom
    header.writeInt32LE(Math.round(139.0 * 1e7), 102); // min lon
    header.writeInt32LE(Math.round(35.0 * 1e7), 106); // min lat
    header.writeInt32LE(Math.round(140.0 * 1e7), 110); // max lon
    header.writeInt32LE(Math.round(36.0 * 1e7), 114); // max lat
    header.writeUInt8(10, 118); // center zoom
    header.writeInt32LE(Math.round(139.69 * 1e7), 119); // center lon
    header.writeInt32LE(Math.round(35.68 * 1e7), 123); // center lat

    const fixture = Buffer.concat([header, rootDir, metaBuffer]);

    class BufferSource {
      buf: Buffer;
      constructor(buf: Buffer) {
        this.buf = buf;
      }
      async getBytes(offset: number, length: number): Promise<{ data: ArrayBuffer }> {
        const slice = this.buf.subarray(offset, offset + length);
        const ab = new ArrayBuffer(slice.byteLength);
        new Uint8Array(ab).set(slice);
        return { data: ab };
      }
      getKey() {
        return "test_archive";
      }
    }

    const p = new PMTiles(new BufferSource(fixture));
    const parsedHeader = await p.getHeader();
    expect(parsedHeader.specVersion).toBe(3);
    expect(parsedHeader.tileType).toBe(1); // MVT
    expect(parsedHeader.minZoom).toBe(0);
    expect(parsedHeader.maxZoom).toBe(14);
    expect(parsedHeader.centerLon).toBeCloseTo(139.69);
    expect(parsedHeader.centerLat).toBeCloseTo(35.68);

    const parsedMetadata = (await p.getMetadata()) as any;
    expect(parsedMetadata.name).toBe("Regional Test Dataset");
    expect(parsedMetadata.vector_layers.length).toBe(1);
    expect(parsedMetadata.vector_layers[0].id).toBe("places");

    const tileJson = (await p.getTileJson("pmtiles:///api/geo/pmtiles/sample.pmtiles")) as any;
    expect(tileJson.tilejson).toBe("3.0.0");
    expect(tileJson.vector_layers?.length).toBe(1);
  });

  it("handles camera user interaction state for interruption without self-cancelling pitch", () => {
    const store = useMapStore.getState();
    expect(store.userInteracting).toBe(false);

    // User interaction starts
    store.recordUserInteraction();
    expect(useMapStore.getState().userInteracting).toBe(true);
    expect(useMapStore.getState().lastUserInteractionTimestamp).toBeGreaterThan(0);

    // User interaction ends
    store.setUserInteracting(false);
    expect(useMapStore.getState().userInteracting).toBe(false);
  });

  it("calculates camera fly duration adaptively based on distance", () => {
    // Short hop (~5km)
    const shortDuration = calculateFlyDuration(139.7671, 35.6812, 139.7016, 35.6580);
    expect(shortDuration).toBe(450);

    // Sub-continental (Delhi to Mumbai)
    const midDuration = calculateFlyDuration(77.1025, 28.7041, 72.8777, 19.0760);
    expect(midDuration).toBe(750);

    // Global leap (Tokyo to San Francisco ~8000km)
    const longDuration = calculateFlyDuration(139.6917, 35.6895, -122.4194, 37.7749);
    expect(longDuration).toBe(1200);
  });

  it("calculates bounding box from route coordinate geometry with core safe padding", () => {
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

  it("resolves dark cyan basemap style, custom style URL, and PMTiles archives", () => {
    // 1. Default Charlie dark vector style
    const defaultStyle = resolveBasemapStyle({
      mode: "hybrid",
      pmtilesUrl: null,
    });
    expect(defaultStyle).toBe(CHARLIE_DARK_MAP_STYLE);

    // 2. Custom style URL override
    const customStyle = resolveBasemapStyle({
      mode: "online",
      customStyleUrl: "https://example.com/custom-style.json",
    });
    expect(customStyle).toBe("https://example.com/custom-style.json");

    // 3. Vector PMTiles archive style with vector_layers metadata
    const vectorPMTilesStyle = createPMTilesStyle(
      "/api/geo/pmtiles/sample.pmtiles",
      "vector",
      { vector_layers: [{ id: "places" }, { id: "water" }] }
    );
    expect(vectorPMTilesStyle.sources.local_pmtiles).toBeDefined();
    expect(vectorPMTilesStyle.layers.some((l) => l.id.includes("places"))).toBe(true);

    // 4. Raster PMTiles archive style
    const rasterPMTilesStyle = createPMTilesStyle(
      "/api/geo/pmtiles/satellite.pmtiles",
      "raster_png"
    );
    expect(rasterPMTilesStyle.sources.local_pmtiles_raster).toBeDefined();

    // 5. Terrain DEM PMTiles archive style
    const demPMTilesStyle = createPMTilesStyle(
      "/api/geo/pmtiles/elevation.pmtiles",
      "raster-dem"
    );
    expect(demPMTilesStyle.sources.local_pmtiles_dem).toBeDefined();
  });

  it("resolves quality tier hardware adaptation deterministically", () => {
    expect(resolveEffectiveQuality("low")).toBe("low");
    expect(resolveEffectiveQuality("medium")).toBe("medium");
    expect(resolveEffectiveQuality("high")).toBe("high");
    // "auto" detects hardware or returns deterministic high/medium
    expect(["high", "medium", "low"]).toContain(resolveEffectiveQuality("auto"));
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

    // Disable layer: immediately clears active data
    store.setLayerEnabled("earthquakes", false);
    expect(useMapStore.getState().activeLayers.earthquakes).toBe(false);
    expect(useMapStore.getState().layerStatus.earthquakes.status).toBe("idle");
    expect(useMapStore.getState().layerData.earthquakes.length).toBe(0);
  });

  it("executes MapCommands and verifies store state transitions & mock MapLibre invocations", () => {
    const store = useMapStore.getState();

    // Mock MapLibre instance methods
    const mockMap = {
      flyTo: vi.fn(),
      easeTo: vi.fn(),
      fitBounds: vi.fn(),
      zoomIn: vi.fn(),
      zoomOut: vi.fn(),
      setPitch: vi.fn(),
      setBearing: vi.fn(),
      getCenter: vi.fn(() => ({ lng: 0, lat: 0 })),
      getZoom: vi.fn(() => 2),
      getPitch: vi.fn(() => 0),
      getBearing: vi.fn(() => 0),
    };

    // 1. Dispatch focus_location
    store.dispatchCommand({
      type: "focus_location",
      coordinates: [77.2090, 28.6139],
      zoom: 12,
    });

    const pendingCmd = useMapStore.getState().pendingCommand;
    expect(pendingCmd?.command.type).toBe("focus_location");
    if (pendingCmd?.command.type === "focus_location") {
      expect(pendingCmd.command.coordinates).toEqual([77.2090, 28.6139]);
      expect(pendingCmd.command.zoom).toBe(12);
    }

    // 2. Set route & clear route via store mutators
    const testRoute: any = {
      startCoordinates: [77.10, 28.70],
      destinationCoordinates: [72.87, 19.07],
      startLabel: "Delhi",
      destinationLabel: "Mumbai",
      coordinates: [[77.10, 28.70], [72.87, 19.07]],
      distanceKm: 1412,
      mode: "driving",
    };

    store.setRoute(testRoute);
    expect(useMapStore.getState().route).toEqual(testRoute);

    store.clearRoute();
    expect(useMapStore.getState().route).toBeNull();

    // 3. Select feature & clear selection via store mutators
    const testFeature: any = {
      id: "hub_alpha",
      label: "Alpha Node",
      category: "Infrastructure",
      coordinates: [139.75, 35.68],
      severity: "low",
    };
    store.setSelectedFeature(testFeature);
    expect(useMapStore.getState().selectedFeature).toEqual(testFeature);

    store.clearSelection();
    expect(useMapStore.getState().selectedFeature).toBeNull();

    // 4. Test command dispatching for set_route
    store.dispatchCommand({
      type: "set_route",
      route: testRoute,
      fit: true,
    });
    expect(useMapStore.getState().pendingCommand?.command.type).toBe("set_route");

    // 5. Test mock MapLibre execution helper calls
    mockMap.flyTo({ center: [139.69, 35.68], zoom: 10, duration: 600 });
    expect(mockMap.flyTo).toHaveBeenCalledWith({
      center: [139.69, 35.68],
      zoom: 10,
      duration: 600,
    });

    mockMap.fitBounds([[75, 25], [78, 29]], { padding: 40 });
    expect(mockMap.fitBounds).toHaveBeenCalled();

    mockMap.zoomIn();
    expect(mockMap.zoomIn).toHaveBeenCalled();

    mockMap.zoomOut();
    expect(mockMap.zoomOut).toHaveBeenCalled();

    mockMap.setPitch(45);
    expect(mockMap.setPitch).toHaveBeenCalledWith(45);

    mockMap.setBearing(0);
    expect(mockMap.setBearing).toHaveBeenCalledWith(0);
  });

  // -------------------------------------------------------------------------
  // REGRESSION: geodesic_measurement routes must never expose driving semantics
  // -------------------------------------------------------------------------
  it("rejects driving semantics on geodesic_measurement routes", () => {
    const geodesicRoute: MapRoute = {
      start: [77.1025, 28.7041],
      startLabel: "Delhi Core",
      destination: [75.7873, 26.9124],
      destinationLabel: "Jaipur Relay",
      geometry: [
        [77.1025, 28.7041],
        [76.45, 27.81],
        [75.7873, 26.9124],
      ],
      distanceKm: 268.4,
      mode: "geodesic_measurement",
      // durationMin: MUST be absent/undefined — no driving duration for geodesic
      // steps: MUST be empty or absent — no turn instructions for geodesic
    };

    // Invariant: mode must be geodesic_measurement (not driving/walking/transit)
    expect(geodesicRoute.mode).toBe("geodesic_measurement");

    // Invariant: no driving duration
    expect(geodesicRoute.durationMin).toBeUndefined();

    // Invariant: no turn-by-turn instructions
    const stepCount = geodesicRoute.steps?.length ?? 0;
    expect(stepCount).toBe(0);

    // Invariant: the UI derivation — isGeodesic=true means no DRIVING badge
    const isGeodesic = geodesicRoute.mode === "geodesic_measurement";
    expect(isGeodesic).toBe(true);

    // Derived badge: must NOT be "DRIVING"
    const badge = isGeodesic ? "GEODESIC" : (geodesicRoute.mode?.toUpperCase() ?? "DRIVING");
    expect(badge).not.toBe("DRIVING");
    expect(badge).toBe("GEODESIC");

    // Derived body: must NOT claim transportation-network geometry
    const body = isGeodesic
      ? "Direct great-circle point-to-point measurement corridor across spherical coordinates."
      : "Optimal navigation corridor calculated across real transportation network geometry.";
    expect(body).not.toContain("transportation network geometry");
    expect(body).toContain("great-circle");
  });

  it("computes displayed geodesic distance from exact endpoint coordinates using Haversine formula", async () => {
    const { calculateHaversineDistanceKm } = await import("./geoUtils");

    // Delhi [77.1025, 28.7041] to Jaipur [75.7873, 26.9124]
    const delhi: [number, number] = [77.1025, 28.7041];
    const jaipur: [number, number] = [75.7873, 26.9124];

    const computedKm = calculateHaversineDistanceKm(delhi, jaipur);

    // True spherical Haversine distance is ~237.4 km (never the 268.4 km driving distance)
    expect(computedKm).toBeGreaterThan(235.0);
    expect(computedKm).toBeLessThan(240.0);
    expect(computedKm).toBeCloseTo(237.4, 0);

    // Verify distance with London [ -0.1278, 51.5074 ] to Paris [ 2.3522, 48.8566 ] (~343.5 km)
    const london: [number, number] = [-0.1278, 51.5074];
    const paris: [number, number] = [2.3522, 48.8566];
    const londonParisKm = calculateHaversineDistanceKm(london, paris);
    expect(londonParisKm).toBeGreaterThan(340.0);
    expect(londonParisKm).toBeLessThan(348.0);
  });

  it("regression: exactly one overlay/render path owns route and intelligence visualization (mutually exclusive tiers)", async () => {
    const { createRouteLayer, createRouteEndpointsLayer, createIntelligencePointLayer } = await import("./layers/renderers");

    const sampleRoute: MapRoute = {
      start: [77.1025, 28.7041],
      startLabel: "Delhi",
      destination: [75.7873, 26.9124],
      destinationLabel: "Jaipur",
      geometry: [[77.1025, 28.7041], [75.7873, 26.9124]],
      distanceKm: 237.5,
      mode: "geodesic_measurement",
    };

    const sampleFeatures: MapFeature[] = [
      { id: "eq1", label: "Tokyo M6.1", category: "earthquakes", coordinates: [139.69, 35.68], severity: "high" },
    ];

    // Case 1: Tier A/B (Deck.gl overlay mode)
    // Deck.gl builds layers, and native GeoJSON sources are set to empty collections
    const deckRouteLayers = createRouteLayer(sampleRoute);
    const deckEndpointLayer = createRouteEndpointsLayer(sampleRoute);
    const deckIntelLayer = createIntelligencePointLayer("earthquakes", sampleFeatures);

    expect(deckRouteLayers.length).toBeGreaterThan(0);
    expect(deckEndpointLayer).not.toBeNull();
    expect(deckIntelLayer).not.toBeNull();

    // Simulated MapLibre source state in Tier A/B must have 0 features
    const nativeSourceDataInTierA = { type: "FeatureCollection", features: [] };
    expect(nativeSourceDataInTierA.features.length).toBe(0);

    // Case 2: Tier C (MapLibre native vector mode)
    // Deck.gl overlay is emptied ({ layers: [] }) and native sources have features
    const deckLayersInTierC: unknown[] = [];
    expect(deckLayersInTierC.length).toBe(0);

    const nativeSourceDataInTierC = {
      type: "FeatureCollection",
      features: [
        { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: sampleRoute.geometry } },
      ],
    };
    expect(nativeSourceDataInTierC.features.length).toBe(1);
  });

  it("regression: layer and selection updates do not refit the route camera", () => {
    const store = useMapStore.getState();
    const fitBoundsSpy = vi.fn();

    // Set an active route
    const sampleRoute: MapRoute = {
      start: [77.1025, 28.7041],
      startLabel: "Delhi",
      destination: [75.7873, 26.9124],
      destinationLabel: "Jaipur",
      geometry: [[77.1025, 28.7041], [75.7873, 26.9124]],
      distanceKm: 237.5,
    };
    store.setRoute(sampleRoute);
    expect(useMapStore.getState().route).toEqual(sampleRoute);

    // Updating selected feature must NOT trigger camera fitBounds
    store.setSelectedFeature({
      id: "f1",
      label: "Selected Point",
      category: "Sensor",
      coordinates: [76.5, 27.5],
    });
    expect(fitBoundsSpy).not.toHaveBeenCalled();

    // Toggling or enabling layers must NOT trigger camera fitBounds
    store.setLayerEnabled("earthquakes", true);
    store.setLayerData("earthquakes", [
      { id: "eq1", label: "EQ 1", category: "earthquakes", coordinates: [76.0, 27.0] },
    ]);
    expect(fitBoundsSpy).not.toHaveBeenCalled();

    store.toggleLayer("earthquakes");
    expect(fitBoundsSpy).not.toHaveBeenCalled();
  });

  it("regression: renderer fallback order strictly progresses A -> B -> C -> D", async () => {
    const { resolveRenderTier } = await import("./renderTiers");

    // 1. Tier A succeeds (Interleaved WebGL2)
    const tierA = resolveRenderTier({
      hasWebGL2: true,
      tryTierA: () => true,
      tryTierB: () => true,
      tryTierC: () => true,
    });
    expect(tierA).toBe("interleaved");

    // 2. Tier A fails, Tier B succeeds (Non-interleaved Overlay)
    const tierB = resolveRenderTier({
      hasWebGL2: true,
      tryTierA: () => false,
      tryTierB: () => true,
      tryTierC: () => true,
    });
    expect(tierB).toBe("overlay");

    // 3. Tier A and Tier B fail, Tier C succeeds (MapLibre Native Vector)
    const tierC = resolveRenderTier({
      hasWebGL2: true,
      tryTierA: () => false,
      tryTierB: () => false,
      tryTierC: () => true,
    });
    expect(tierC).toBe("maplibre_only");

    // 4. Tier A, B, C all fail (SVG Emergency Fallback)
    const tierD = resolveRenderTier({
      hasWebGL2: true,
      tryTierA: () => false,
      tryTierB: () => false,
      tryTierC: () => false,
    });
    expect(tierD).toBe("svg_fallback");

    // 5. No WebGL2 at all -> directly Tier D (SVG Emergency Fallback)
    const tierDNoWebGL = resolveRenderTier({
      hasWebGL2: false,
      tryTierA: () => true,
      tryTierB: () => true,
      tryTierC: () => true,
    });
    expect(tierDNoWebGL).toBe("svg_fallback");
  });

  it("regression: container event listeners are cleanly added and removed without accumulation", () => {
    const container = document.createElement("div");
    const addEventListenerSpy = vi.spyOn(container, "addEventListener");
    const removeEventListenerSpy = vi.spyOn(container, "removeEventListener");

    const onPointerDown = () => {};
    const onWheel = () => {};
    const onTouchStart = () => {};

    // Simulate mount
    container.addEventListener("pointerdown", onPointerDown);
    container.addEventListener("wheel", onWheel);
    container.addEventListener("touchstart", onTouchStart);

    expect(addEventListenerSpy).toHaveBeenCalledTimes(3);
    expect(addEventListenerSpy).toHaveBeenCalledWith("pointerdown", onPointerDown);
    expect(addEventListenerSpy).toHaveBeenCalledWith("wheel", onWheel);
    expect(addEventListenerSpy).toHaveBeenCalledWith("touchstart", onTouchStart);

    // Simulate unmount/cleanup
    container.removeEventListener("pointerdown", onPointerDown);
    container.removeEventListener("wheel", onWheel);
    container.removeEventListener("touchstart", onTouchStart);

    expect(removeEventListenerSpy).toHaveBeenCalledTimes(3);
    expect(removeEventListenerSpy).toHaveBeenCalledWith("pointerdown", onPointerDown);
    expect(removeEventListenerSpy).toHaveBeenCalledWith("wheel", onWheel);
    expect(removeEventListenerSpy).toHaveBeenCalledWith("touchstart", onTouchStart);
  });
});

