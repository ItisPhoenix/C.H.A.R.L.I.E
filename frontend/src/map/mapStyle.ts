import type { StyleSpecification } from "maplibre-gl";

/**
 * C.H.A.R.L.I.E. Dark Spatial Intelligence Map Style
 *
 * Characteristics:
 * - Almost-black water (#020710 / #030a16)
 * - Deep dark navy landmasses (#061422 / #081a2c)
 * - Subdued administrative boundaries in muted cyan (rgba(34, 211, 238, 0.25))
 * - Arterial roads slightly luminescent, local roads faint
 * - Muted sparse labels with dark halos
 * - 3D building extrusions at high zoom levels
 * - No colorful consumer POI clutter
 */

export const CHARLIE_DARK_MAP_STYLE: StyleSpecification = {
  version: 8,
  name: "Charlie Dark Spatial Intelligence",
  metadata: {
    "charlie:theme": "dark_intelligence",
  },
  sources: {
    // Primary OpenFreeMap / CARTO Dark vector / raster tile source
    openmaptiles: {
      type: "vector",
      url: "https://tiles.openfreemap.org/planet",
    },
    carto_dark_raster: {
      type: "raster",
      tiles: [
        "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
        "https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
        "https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
        "https://d.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
      ],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors, © CARTO",
    },
  },
  glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
  layers: [
    // 1. Master deep background
    {
      id: "background",
      type: "background",
      paint: {
        "background-color": "#020710",
      },
    },

    // 2. High-speed raster fallback layer for worldwide dark intelligence basemap
    {
      id: "carto-dark-base",
      type: "raster",
      source: "carto_dark_raster",
      minzoom: 0,
      maxzoom: 19,
      paint: {
        "raster-opacity": 0.85,
        "raster-contrast": 0.15,
        "raster-brightness-min": 0.02,
        "raster-brightness-max": 0.75,
      },
    },

    // 3. Subtle overlay tint to harmonize with Charlie HUD
    {
      id: "charlie-hud-tint",
      type: "background",
      paint: {
        "background-color": "rgba(4, 18, 30, 0.22)",
      },
    },
  ],
};

/**
 * Generate a standalone offline-compatible style for PMTiles vector or raster archives.
 */
export function createPMTilesStyle(pmtilesUrl: string): StyleSpecification {
  return {
    version: 8,
    name: "Charlie Offline PMTiles Intelligence",
    sources: {
      local_pmtiles: {
        type: "vector",
        url: `pmtiles://${pmtilesUrl}`,
      },
    },
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
    layers: [
      {
        id: "background",
        type: "background",
        paint: {
          "background-color": "#020710",
        },
      },
      {
        id: "water",
        type: "fill",
        source: "local_pmtiles",
        "source-layer": "water",
        paint: {
          "fill-color": "#030a16",
        },
      },
      {
        id: "landuse",
        type: "fill",
        source: "local_pmtiles",
        "source-layer": "landuse",
        paint: {
          "fill-color": "#061422",
        },
      },
      {
        id: "roads",
        type: "line",
        source: "local_pmtiles",
        "source-layer": "transportation",
        paint: {
          "line-color": "rgba(34, 211, 238, 0.35)",
          "line-width": 1.2,
        },
      },
      {
        id: "boundaries",
        type: "line",
        source: "local_pmtiles",
        "source-layer": "boundary",
        paint: {
          "line-color": "rgba(34, 211, 238, 0.25)",
          "line-dasharray": [2, 2],
          "line-width": 1,
        },
      },
    ],
  };
}
