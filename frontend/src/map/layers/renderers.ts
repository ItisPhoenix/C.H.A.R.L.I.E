import { ScatterplotLayer, PathLayer } from "@deck.gl/layers";
import type { MapFeature, MapRoute } from "../types";

/**
 * Creates a high-performance Deck.gl ScatterplotLayer for intelligence points.
 */
export function createIntelligencePointLayer(
  layerId: string,
  data: MapFeature[],
  onSelectFeature?: (feature: MapFeature) => void
): ScatterplotLayer<MapFeature> {
  return new ScatterplotLayer<MapFeature>({
    id: `intel-points-${layerId}`,
    data,
    pickable: true,
    opacity: 0.85,
    stroked: true,
    filled: true,
    radiusScale: 6,
    radiusMinPixels: 4.5,
    radiusMaxPixels: 24,
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
  onSelectRoute?: () => void
): PathLayer<unknown>[] {
  const routeData = [
    {
      path: route.geometry,
      color: [0, 240, 255],
    },
  ];

  // Glow line (wide translucent path)
  const glowLayer = new PathLayer({
    id: "route-glow-layer",
    data: routeData,
    pickable: false,
    widthScale: 1,
    widthMinPixels: 8,
    getPath: (d: { path: [number, number][] }) => d.path,
    getColor: [0, 240, 255, 60],
    capRounded: true,
    jointRounded: true,
  });

  // Core bright line
  const coreLayer = new PathLayer({
    id: "route-core-layer",
    data: routeData,
    pickable: true,
    widthScale: 1,
    widthMinPixels: 3,
    getPath: (d: { path: [number, number][] }) => d.path,
    getColor: [255, 255, 255, 230],
    capRounded: true,
    jointRounded: true,
    onClick: () => {
      if (onSelectRoute) onSelectRoute();
    },
  });

  return [glowLayer, coreLayer];
}

/**
 * Creates selection pulse radar ring around the selected feature.
 */
export function createSelectionPulseLayer(
  selectedFeature: MapFeature | null
): ScatterplotLayer<MapFeature> | null {
  if (!selectedFeature) return null;

  return new ScatterplotLayer<MapFeature>({
    id: "selected-feature-pulse",
    data: [selectedFeature],
    pickable: false,
    stroked: true,
    filled: false,
    radiusScale: 1,
    radiusMinPixels: 14,
    lineWidthMinPixels: 2,
    getPosition: (d: MapFeature) => [d.coordinates[0], d.coordinates[1]],
    getLineColor: [0, 240, 255, 255],
  });
}

function parseHexColor(hex: string, alpha: number = 255): [number, number, number, number] {
  let c = hex.replace("#", "");
  if (c.length === 3) {
    c = c[0] + c[0] + c[1] + c[1] + c[2] + c[2];
  }
  const num = parseInt(c, 16);
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255, alpha];
}
