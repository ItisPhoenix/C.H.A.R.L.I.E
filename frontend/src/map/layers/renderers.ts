import { ScatterplotLayer, PathLayer } from "@deck.gl/layers";
import type { MapFeature, MapRoute, QualityTier } from "../types";

/**
 * Deterministically resolves effective quality tier based on explicit choice or runtime/hardware capability.
 */
export function resolveEffectiveQuality(quality: QualityTier = "auto"): "high" | "medium" | "low" {
  if (quality !== "auto") return quality;

  if (typeof navigator !== "undefined") {
    const cores = navigator.hardwareConcurrency || 4;
    const memory = (navigator as unknown as { deviceMemory?: number }).deviceMemory || 8;
    if (cores <= 2 || memory <= 2) {
      return "low";
    }
    if (cores <= 4 || memory <= 4) {
      return "medium";
    }
  }
  return "high";
}

/**
 * Creates a high-performance Deck.gl ScatterplotLayer for intelligence points.
 */
export function createIntelligencePointLayer(
  layerId: string,
  data: MapFeature[],
  onSelectFeature?: (feature: MapFeature) => void,
  quality: QualityTier = "auto"
): ScatterplotLayer<MapFeature> {
  const effectiveQuality = resolveEffectiveQuality(quality);

  // Quality tier adaptations: cap feature count and simplify stroke geometry
  let displayData = data;
  let stroked = true;
  let radiusMin = 4.5;

  if (effectiveQuality === "low") {
    displayData = data.slice(0, 100);
    stroked = false;
    radiusMin = 3.5;
  } else if (effectiveQuality === "medium") {
    displayData = data.slice(0, 500);
  }

  return new ScatterplotLayer<MapFeature>({
    id: `intel-points-${layerId}`,
    data: displayData,
    pickable: true,
    opacity: 0.85,
    stroked,
    filled: true,
    radiusScale: 6,
    radiusMinPixels: radiusMin,
    radiusMaxPixels: effectiveQuality === "low" ? 16 : 24,
    lineWidthMinPixels: 1.5,
    getPosition: (d: MapFeature) => [d.coordinates[0], d.coordinates[1]],
    getRadius: (d: MapFeature) => {
      if (d.severity === "critical") return 2400;
      if (d.severity === "high") return 1800;
      if (d.severity === "medium") return 1200;
      return 800;
    },
    getFillColor: (d: MapFeature) => {
      if (d.color) {
        return parseHexColor(d.color, 190);
      }
      if (d.severity === "critical") return [239, 68, 68, 220]; // Red
      if (d.severity === "high") return [249, 115, 22, 210];   // Orange
      if (d.severity === "medium") return [234, 179, 8, 200];  // Yellow
      return [0, 240, 255, 190];                               // Cyan
    },
    getLineColor: [255, 255, 255, 220],
    onClick: (info) => {
      if (info.object && onSelectFeature) {
        onSelectFeature(info.object as MapFeature);
      }
    },
  });
}

/**
 * Creates Deck.gl PathLayers for navigation routing corridors.
 */
export function createRouteLayer(
  route: MapRoute,
  onSelectRoute?: () => void,
  quality: QualityTier = "auto"
): PathLayer<unknown>[] {
  const effectiveQuality = resolveEffectiveQuality(quality);
  const isGeodesic = route.mode === "geodesic_measurement";
  const lineColor: [number, number, number] = isGeodesic ? [56, 189, 248] : [0, 240, 255];

  const geometry = (route.geometry || (route as any).coordinates || []) as [number, number][];
  const routeData = [
    {
      path: geometry,
      color: lineColor,
    },
  ];

  // Core bright line
  const coreLayer = new PathLayer({
    id: "route-core-layer",
    data: routeData,
    pickable: true,
    widthScale: 1,
    widthMinPixels: isGeodesic ? 2 : 3,
    getPath: (d: { path: [number, number][] }) => d.path,
    getColor: isGeodesic ? [56, 189, 248, 220] : [255, 255, 255, 230],
    capRounded: true,
    jointRounded: true,
    onClick: () => {
      if (onSelectRoute) onSelectRoute();
    },
  });

  // In Low quality tier, omit the outer glow layer
  if (effectiveQuality === "low") {
    return [coreLayer];
  }

  // Glow line (wide translucent path)
  const glowLayer = new PathLayer({
    id: "route-glow-layer",
    data: routeData,
    pickable: false,
    widthScale: 1,
    widthMinPixels: isGeodesic ? 6 : 8,
    getPath: (d: { path: [number, number][] }) => d.path,
    getColor: [...lineColor, 60],
    capRounded: true,
    jointRounded: true,
  });

  return [glowLayer, coreLayer];
}

/**
 * Creates Deck.gl ScatterplotLayer for route origin & destination endpoints.
 */
export function createRouteEndpointsLayer(
  route: MapRoute,
  quality: QualityTier = "auto"
): ScatterplotLayer<any> | null {
  const geometry = (route.geometry || (route as any).coordinates || []) as [number, number][];
  if (geometry.length < 2) return null;
  const effectiveQuality = resolveEffectiveQuality(quality);

  const endpoints = [
    {
      id: "origin",
      coordinates: geometry[0],
      type: "origin",
      label: route.startLabel || "Origin",
      color: [34, 211, 238, 240] as [number, number, number, number],
    },
    {
      id: "destination",
      coordinates: geometry[geometry.length - 1],
      type: "destination",
      label: route.destinationLabel || "Destination",
      color: [244, 63, 94, 240] as [number, number, number, number],
    },
  ];

  return new ScatterplotLayer({
    id: "route-endpoints-layer",
    data: endpoints,
    pickable: true,
    stroked: true,
    filled: true,
    lineWidthMinPixels: 2,
    radiusMinPixels: effectiveQuality === "low" ? 5 : 6,
    radiusMaxPixels: effectiveQuality === "low" ? 12 : 16,
    getPosition: (d: any) => d.coordinates,
    getRadius: 1000,
    getFillColor: (d: any) => d.color,
    getLineColor: [255, 255, 255, 255],
  });
}

/**
 * Creates selection pulse radar ring around the selected feature.
 */
export function createSelectionPulseLayer(
  selectedFeature: MapFeature | null,
  quality: QualityTier = "auto"
): ScatterplotLayer<MapFeature> | null {
  if (!selectedFeature) return null;
  const effectiveQuality = resolveEffectiveQuality(quality);

  return new ScatterplotLayer<MapFeature>({
    id: "selected-feature-pulse",
    data: [selectedFeature],
    pickable: false,
    stroked: true,
    filled: false,
    lineWidthMinPixels: 2,
    radiusMinPixels: effectiveQuality === "low" ? 12 : 16,
    radiusMaxPixels: effectiveQuality === "low" ? 24 : 32,
    getPosition: (d: MapFeature) => [d.coordinates[0], d.coordinates[1]],
    getRadius: 3000,
    getLineColor: [0, 240, 255, 240],
  });
}

function parseHexColor(hex: string, alpha: number = 255): [number, number, number, number] {
  const cleanHex = hex.replace("#", "");
  if (cleanHex.length === 6) {
    const num = parseInt(cleanHex, 16);
    return [(num >> 16) & 255, (num >> 8) & 255, num & 255, alpha];
  }
  return [0, 240, 255, alpha];
}
