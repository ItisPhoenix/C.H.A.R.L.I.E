/**
 * C.H.A.R.L.I.E. V1 — Geospatial & Spatial Intelligence Core Types
 */

export interface MapCameraState {
  longitude: number;
  latitude: number;
  zoom: number;
  pitch: number;
  bearing: number;
}

export type ProviderMode = "hybrid" | "online" | "offline";
export type QualityTier = "auto" | "high" | "medium" | "low";
export type LayerStatus = "idle" | "loading" | "ready" | "unconfigured" | "error";

export interface ProviderHealth {
  mode: ProviderMode;
  onlineHealthy: boolean;
  offlineHealthy: boolean;
  activeTileSource: string;
  capabilities: {
    terrain: boolean;
    buildings3d: boolean;
    globe: boolean;
  };
  lastError?: string;
}

export interface MapFeature {
  id: string;
  label: string;
  category: string;
  description?: string;
  coordinates: [number, number]; // [lon, lat]
  severity?: "low" | "normal" | "medium" | "high" | "critical";
  source?: string;
  timestamp?: string;
  properties?: Record<string, unknown>;
  color?: string;
}

export interface RouteStep {
  instruction: string;
  distance: string;
  duration?: string;
}

export interface MapRoute {
  start: [number, number];
  startLabel: string;
  destination: [number, number];
  destinationLabel: string;
  geometry: [number, number][]; // [[lon, lat], ...]
  distanceKm?: number;
  durationMin?: number;
  steps?: RouteStep[];
  mode?: "driving" | "walking" | "transit" | "vector";
  provider?: string;
}

export type MapCommand =
  | { type: "fly_to"; longitude: number; latitude: number; zoom?: number; pitch?: number; bearing?: number; durationMs?: number }
  | { type: "ease_to"; longitude: number; latitude: number; zoom?: number; pitch?: number; bearing?: number; durationMs?: number }
  | { type: "fit_bounds"; bounds: [[number, number], [number, number]]; padding?: { top?: number; bottom?: number; left?: number; right?: number }; durationMs?: number }
  | { type: "zoom_in" }
  | { type: "zoom_out" }
  | { type: "reset_north" }
  | { type: "set_pitch"; pitch: number }
  | { type: "set_bearing"; bearing: number }
  | { type: "focus_location"; coordinates: [number, number]; zoom?: number }
  | { type: "select_feature"; feature: MapFeature | null; flyTo?: boolean }
  | { type: "set_layer"; layerId: string; enabled: boolean }
  | { type: "toggle_layer"; layerId: string }
  | { type: "set_route"; route: MapRoute; fit?: boolean }
  | { type: "clear_route" }
  | { type: "clear_selection" }
  | { type: "reset_map" };

export interface IntelligenceLayerDefinition {
  id: string;
  label: string;
  category: "Environment" | "Weather" | "Security" | "Infrastructure" | "Aviation" | "Maritime" | "Cyber" | "Strategic" | "Economic";
  defaultEnabled: false;
  attribution: string;
  requiresCredential?: boolean;
  fetcher?: (signal?: AbortSignal) => Promise<MapFeature[]>;
}
