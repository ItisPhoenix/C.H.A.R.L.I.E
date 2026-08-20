import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import * as maplibregl from "maplibre-gl";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { MapEngine } from "./MapEngine";
import { useMapStore } from "./mapStore";

const mockMapInstance = {
  on: vi.fn(),
  off: vi.fn(),
  once: vi.fn(),
  addControl: vi.fn(),
  removeControl: vi.fn(),
  remove: vi.fn(),
  resize: vi.fn(),
  getStyle: vi.fn(() => ({ layers: [{ id: "osm" }] })),
  getSource: vi.fn(() => null),
  addSource: vi.fn(),
  addLayer: vi.fn(),
  getCenter: vi.fn(() => ({ lng: 15, lat: 25 })),
  getZoom: vi.fn(() => 2),
  getPitch: vi.fn(() => 0),
  getBearing: vi.fn(() => 0),
  isMoving: vi.fn(() => false),
  stop: vi.fn(),
  flyTo: vi.fn(),
  fitBounds: vi.fn(),
};

vi.mock("maplibre-gl", () => {
  function MockMap() {
    return mockMapInstance;
  }
  return {
    Map: vi.fn().mockImplementation(MockMap),
    NavigationControl: vi.fn(),
    ScaleControl: vi.fn(),
    IControl: vi.fn(),
    addProtocol: vi.fn(),
  };
});

vi.mock("@deck.gl/mapbox", () => {
  function MockOverlay(opts: any) {
    return {
      props: opts,
      setProps: vi.fn(),
    };
  }
  return {
    MapboxOverlay: vi.fn().mockImplementation(MockOverlay),
  };
});

describe("MapEngine Component-Level Initialization & Renderer Exclusivity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (window as any).WebGL2RenderingContext = class {};
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation((contextId: string) => {
      if (contextId === "webgl2" || contextId === "2d") {
        return {} as any;
      }
      return null;
    });

    (maplibregl.Map as unknown as ReturnType<typeof vi.fn>).mockImplementation(function () {
      return mockMapInstance;
    });
    (MapboxOverlay as unknown as ReturnType<typeof vi.fn>).mockImplementation(function (opts: { interleaved?: boolean }) {
      return {
        props: opts,
        setProps: vi.fn(),
      };
    });

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

  afterEach(() => {
    cleanup();
  });

  it("initializes in the safe non-interleaved Deck.gl overlay path by default", () => {
    const { container } = render(<MapEngine />);
    expect(maplibregl.Map).toHaveBeenCalled();
    expect(MapboxOverlay).toHaveBeenCalledWith({ interleaved: false });

    // Verify NO duplicate projected SVG elements exist in DOM for Tier A
    const svgOverlays = container.querySelectorAll("svg.pointer-events-none");
    expect(svgOverlays.length).toBe(0);
  });

  it("falls back to the native MapLibre tier if the safe Deck overlay constructor throws", () => {
    let callCount = 0;
    (MapboxOverlay as unknown as ReturnType<typeof vi.fn>).mockImplementation(function (opts: { interleaved?: boolean }) {
      callCount++;
      if (opts.interleaved === false) {
        throw new Error("WebGL2 interleaved not supported");
      }
      return {
        props: opts,
        setProps: vi.fn(),
      };
    });

    render(<MapEngine />);
    expect(callCount).toBe(2);
    expect(MapboxOverlay).toHaveBeenNthCalledWith(1, { interleaved: false });
    expect(MapboxOverlay).toHaveBeenNthCalledWith(2, { interleaved: false });
  });

  it("falls back to Tier C (maplibre_only native) if Deck.gl fails completely", () => {
    (MapboxOverlay as unknown as ReturnType<typeof vi.fn>).mockImplementation(function () {
      throw new Error("Deck.gl totally unsupported");
    });

    render(<MapEngine />);
    expect(maplibregl.Map).toHaveBeenCalled();
    expect(MapboxOverlay).toHaveBeenCalledTimes(2);
  });

  it("falls back to Tier D (svg_fallback) if MapLibre GL constructor throws", () => {
    (maplibregl.Map as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(function () {
      throw new Error("WebGL context creation failed");
    });

    const { container } = render(<MapEngine />);
    expect(screen.getByText(/SPATIAL VECTOR RADAR \(TIER-D\)/i)).toBeInTheDocument();
    // In Tier D, SpatialMapFallback renders SVG elements
    const svgFallback = container.querySelector("svg");
    expect(svgFallback).not.toBeNull();
  });

  it("cleans up MapLibre and container DOM listeners on unmount", () => {
    const { unmount } = render(<MapEngine />);

    expect(mockMapInstance.on).toHaveBeenCalled();
    unmount();
    expect(mockMapInstance.off).toHaveBeenCalled();
    expect(mockMapInstance.remove).toHaveBeenCalled();
  });
});
