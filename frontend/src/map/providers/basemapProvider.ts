import type { StyleSpecification } from "maplibre-gl";
import { CHARLIE_DARK_MAP_STYLE, createPMTilesStyle } from "../mapStyle";
import type { ProviderMode } from "../types";
import { initPMTilesProtocol } from "./pmtilesProvider";

export interface BasemapConfig {
  mode: ProviderMode;
  pmtilesUrl?: string | null;
  pmtilesTileType?: string;
  pmtilesMetadata?: { vector_layers?: Array<{ id: string }> } | null;
  customStyleUrl?: string | null;
  customVectorSourceUrl?: string | null;
}

/**
 * Resolves the authoritative StyleSpecification for MapLibre based on current provider mode.
 *
 * Invariant:
 * - https://tiles.openfreemap.org/planet is a vector source referenced INSIDE CHARLIE_DARK_MAP_STYLE,
 *   never returned raw as a style URL.
 * - Online/hybrid default always returns CHARLIE_DARK_MAP_STYLE.
 */
export function resolveBasemapStyle(config: BasemapConfig): StyleSpecification | string {
  // 1. Offline Mode / Hybrid with local PMTiles configured
  if (config.mode === "offline" || (config.mode === "hybrid" && config.pmtilesUrl)) {
    if (config.pmtilesUrl) {
      initPMTilesProtocol();
      return createPMTilesStyle(config.pmtilesUrl, config.pmtilesTileType || "vector", config.pmtilesMetadata);
    }
  }

  // 2. Custom Style URL (if explicitly provided by user/settings)
  if (config.customStyleUrl) {
    return config.customStyleUrl;
  }

  // 3. Custom Vector Source URL override (creates Charlie Dark style pointing to custom vector source)
  if (config.customVectorSourceUrl) {
    return {
      ...CHARLIE_DARK_MAP_STYLE,
      sources: {
        ...CHARLIE_DARK_MAP_STYLE.sources,
        openfreemap: {
          type: "vector",
          url: config.customVectorSourceUrl,
          attribution: "Custom Vector Tiles",
        },
      },
    };
  }

  // 4. Default: Charlie Dark Spatial Intelligence Map Style
  return CHARLIE_DARK_MAP_STYLE;
}
