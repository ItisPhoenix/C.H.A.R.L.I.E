import type { StyleSpecification } from "maplibre-gl";
import { CHARLIE_DARK_MAP_STYLE, createPMTilesStyle } from "../mapStyle";
import type { ProviderMode } from "../types";
import { initPMTilesProtocol } from "./pmtilesProvider";

export interface BasemapConfig {
  mode: ProviderMode;
  pmtilesUrl?: string | null;
  onlineSourceUrl?: string | null;
}

/**
 * Resolves the appropriate StyleSpecification for MapLibre based on current provider mode.
 */
export function resolveBasemapStyle(config: BasemapConfig): StyleSpecification | string {
  // 1. Offline Mode: Prefer PMTiles dataset if configured
  if (config.mode === "offline" || (config.mode === "hybrid" && config.pmtilesUrl)) {
    if (config.pmtilesUrl) {
      initPMTilesProtocol();
      return createPMTilesStyle(config.pmtilesUrl);
    }
  }

  // 2. Custom online URL if provided
  if (config.onlineSourceUrl) {
    return config.onlineSourceUrl;
  }

  // 3. Default: Charlie Dark Spatial Intelligence Map Style
  return CHARLIE_DARK_MAP_STYLE;
}
