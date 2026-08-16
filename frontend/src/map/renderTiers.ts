export type MapRenderTier = "interleaved" | "overlay" | "maplibre_only" | "svg_fallback";

export interface ResolveTierOptions {
  hasWebGL2: boolean;
  tryTierA?: () => boolean;
  tryTierB?: () => boolean;
  tryTierC?: () => boolean;
}

/**
 * Deterministically resolves the MapEngine render tier following the strict fallback ladder:
 * Tier A: MapLibre + Deck.gl interleaved
 * Tier B: MapLibre + Deck.gl overlay (non-interleaved)
 * Tier C: MapLibre native (GeoJSON layers)
 * Tier D: SVG fallback
 */
export function resolveRenderTier(options: ResolveTierOptions): MapRenderTier {
  if (!options.hasWebGL2) {
    return "svg_fallback";
  }
  if (options.tryTierA && options.tryTierA()) {
    return "interleaved";
  }
  if (options.tryTierB && options.tryTierB()) {
    return "overlay";
  }
  if (options.tryTierC && options.tryTierC()) {
    return "maplibre_only";
  }
  return "svg_fallback";
}
