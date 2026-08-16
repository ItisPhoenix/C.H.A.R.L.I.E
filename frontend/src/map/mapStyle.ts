import type { StyleSpecification } from "maplibre-gl";

/**
 * C.H.A.R.L.I.E. Dark Spatial Intelligence Map Style
 *
 * Characteristics:
 * - Almost-black water (#020710 / #030a16)
 * - Deep dark navy landmasses (#061422 / #081a2c)
 * - Subdued administrative boundaries in muted cyan (rgba(34, 211, 238, 0.35))
 * - Arterial roads slightly luminescent (#00f0ff / #38bdf8), local roads faint
 * - Muted sparse labels with dark halos
 * - 3D building extrusions at high zoom levels
 * - Fully compliant legal attribution
 */

export const CHARLIE_DARK_MAP_STYLE: StyleSpecification = {
  version: 8,
  name: "Charlie Dark Spatial Intelligence",
  metadata: {
    "charlie:theme": "dark_intelligence",
  },
  sources: {
    // Primary OpenFreeMap vector tile source
    openfreemap: {
      type: "vector",
      url: "https://tiles.openfreemap.org/planet",
      attribution: "© OpenStreetMap contributors, © OpenFreeMap",
    },
    // High-performance raster fallback underlay
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
    // 1. Deep space base
    {
      id: "background",
      type: "background",
      paint: {
        "background-color": "#020710",
      },
    },

    // 2. High-speed raster foundation layer
    {
      id: "carto-dark-base",
      type: "raster",
      source: "carto_dark_raster",
      minzoom: 0,
      maxzoom: 19,
      paint: {
        "raster-opacity": 0.85,
        "raster-contrast": 0.18,
        "raster-brightness-min": 0.02,
        "raster-brightness-max": 0.72,
      },
    },

    // 3. Vector Water Polygons
    {
      id: "water-vector",
      type: "fill",
      source: "openfreemap",
      "source-layer": "water",
      minzoom: 4,
      paint: {
        "fill-color": "#030a16",
        "fill-opacity": 0.65,
      },
    },

    // 4. Vector Landcover & Parks
    {
      id: "landcover-vector",
      type: "fill",
      source: "openfreemap",
      "source-layer": "landcover",
      minzoom: 5,
      paint: {
        "fill-color": "#061524",
        "fill-opacity": 0.45,
      },
    },

    // 5. Minor / Local Roads
    {
      id: "roads-minor",
      type: "line",
      source: "openfreemap",
      "source-layer": "transportation",
      minzoom: 11,
      filter: ["!in", "class", "motorway", "trunk", "primary", "secondary"],
      paint: {
        "line-color": "rgba(34, 211, 238, 0.18)",
        "line-width": 0.8,
      },
    },

    // 6. Secondary Roads
    {
      id: "roads-secondary",
      type: "line",
      source: "openfreemap",
      "source-layer": "transportation",
      minzoom: 7,
      filter: ["in", "class", "secondary", "tertiary"],
      paint: {
        "line-color": "rgba(34, 211, 238, 0.4)",
        "line-width": 1.2,
      },
    },

    // 7. Primary Arterials
    {
      id: "roads-primary",
      type: "line",
      source: "openfreemap",
      "source-layer": "transportation",
      minzoom: 5,
      filter: ["in", "class", "primary", "trunk"],
      paint: {
        "line-color": "rgba(56, 189, 248, 0.6)",
        "line-width": 1.8,
      },
    },

    // 8. Motorways & Expressways
    {
      id: "roads-motorway",
      type: "line",
      source: "openfreemap",
      "source-layer": "transportation",
      minzoom: 3,
      filter: ["==", "class", "motorway"],
      paint: {
        "line-color": "rgba(0, 240, 255, 0.8)",
        "line-width": 2.2,
      },
    },

    // 9. Administrative Boundaries (State/Province)
    {
      id: "boundaries-state",
      type: "line",
      source: "openfreemap",
      "source-layer": "boundary",
      filter: ["==", "admin_level", 4],
      paint: {
        "line-color": "rgba(34, 211, 238, 0.25)",
        "line-dasharray": [2, 2],
        "line-width": 0.8,
      },
    },

    // 10. Country Boundaries
    {
      id: "boundaries-country",
      type: "line",
      source: "openfreemap",
      "source-layer": "boundary",
      filter: ["<=", "admin_level", 2],
      paint: {
        "line-color": "rgba(34, 211, 238, 0.5)",
        "line-dasharray": [3, 2],
        "line-width": 1.4,
      },
    },

    // 11. 3D Buildings Extrusion (High Zoom)
    {
      id: "buildings-3d",
      type: "fill-extrusion",
      source: "openfreemap",
      "source-layer": "building",
      minzoom: 14,
      paint: {
        "fill-extrusion-color": "#081f33",
        "fill-extrusion-height": ["coalesce", ["get", "render_height"], ["get", "height"], 12],
        "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], ["get", "min_height"], 0],
        "fill-extrusion-opacity": 0.75,
      },
    },

    // 12. City / Place Labels
    {
      id: "place-labels",
      type: "symbol",
      source: "openfreemap",
      "source-layer": "place",
      minzoom: 3,
      layout: {
        "text-field": ["coalesce", ["get", "name:en"], ["get", "name:latin"], ["get", "name"]],
        "text-size": 11,
        "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
        "text-max-width": 8,
      },
      paint: {
        "text-color": "#94a3b8",
        "text-halo-color": "#020710",
        "text-halo-width": 1.5,
      },
    },

    // 13. Charlie Ambient Vignette Overlay
    {
      id: "charlie-hud-tint",
      type: "background",
      paint: {
        "background-color": "rgba(4, 18, 30, 0.15)",
      },
    },
  ],
};

/**
 * Generate a standalone offline-compatible style for PMTiles vector or raster archives.
 */
export function createPMTilesStyle(pmtilesUrl: string, isRaster: boolean = false): StyleSpecification {
  if (isRaster) {
    return {
      version: 8,
      name: "Charlie Offline PMTiles Raster",
      sources: {
        local_pmtiles_raster: {
          type: "raster",
          url: `pmtiles://${pmtilesUrl}`,
          tileSize: 256,
        },
      },
      layers: [
        {
          id: "background",
          type: "background",
          paint: {
            "background-color": "#020710",
          },
        },
        {
          id: "pmtiles-raster-layer",
          type: "raster",
          source: "local_pmtiles_raster",
          paint: {
            "raster-opacity": 0.9,
          },
        },
      ],
    };
  }

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
          "line-color": "rgba(34, 211, 238, 0.35)",
          "line-dasharray": [2, 2],
          "line-width": 1,
        },
      },
    ],
  };
}
