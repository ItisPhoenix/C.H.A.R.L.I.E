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
      paint: {
        "fill-color": "#020710",
        "fill-opacity": 0.95,
      },
    },

    // 4. Vector Landcover & Landuse
    {
      id: "landcover-vector",
      type: "fill",
      source: "openfreemap",
      "source-layer": "landcover",
      paint: {
        "fill-color": "#061422",
        "fill-opacity": 0.65,
      },
    },

    // 5. Minor / Secondary Road Network
    {
      id: "roads-minor",
      type: "line",
      source: "openfreemap",
      "source-layer": "transportation",
      filter: ["!in", "class", "motorway", "trunk", "primary"],
      minzoom: 11,
      paint: {
        "line-color": "rgba(34, 211, 238, 0.15)",
        "line-width": ["interpolate", ["linear"], ["zoom"], 11, 0.5, 16, 1.8],
      },
    },

    // 6. Primary Arterial Roads & Highways
    {
      id: "roads-arterial",
      type: "line",
      source: "openfreemap",
      "source-layer": "transportation",
      filter: ["in", "class", "motorway", "trunk", "primary"],
      minzoom: 6,
      paint: {
        "line-color": "rgba(0, 240, 255, 0.45)",
        "line-width": ["interpolate", ["linear"], ["zoom"], 6, 0.8, 12, 2.4, 16, 4.0],
      },
    },

    // 7. Administrative Country Boundaries
    {
      id: "boundaries-country",
      type: "line",
      source: "openfreemap",
      "source-layer": "boundary",
      filter: ["==", "admin_level", 2],
      paint: {
        "line-color": "rgba(34, 211, 238, 0.45)",
        "line-width": 1.2,
        "line-dasharray": [3, 2],
      },
    },

    // 8. Administrative State / Regional Boundaries
    {
      id: "boundaries-state",
      type: "line",
      source: "openfreemap",
      "source-layer": "boundary",
      filter: [">", "admin_level", 2],
      minzoom: 4,
      paint: {
        "line-color": "rgba(34, 211, 238, 0.25)",
        "line-width": 0.8,
        "line-dasharray": [2, 2],
      },
    },

    // 9. 3D Extruded Buildings (Visible when pitched at high zoom)
    {
      id: "building-3d",
      type: "fill-extrusion",
      source: "openfreemap",
      "source-layer": "building",
      minzoom: 14,
      paint: {
        "fill-extrusion-color": "#0a223a",
        "fill-extrusion-height": ["coalesce", ["get", "render_height"], 15],
        "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
        "fill-extrusion-opacity": 0.85,
      },
    },

    // 10. Country & Capital City Place Labels
    {
      id: "place-labels-country",
      type: "symbol",
      source: "openfreemap",
      "source-layer": "place",
      filter: ["in", "class", "country", "state"],
      layout: {
        "text-field": ["coalesce", ["get", "name_en"], ["get", "name"]],
        "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 2, 9, 6, 13],
        "text-transform": "uppercase",
        "text-letter-spacing": 0.15,
        "text-max-width": 8,
      },
      paint: {
        "text-color": "rgba(148, 163, 184, 0.85)",
        "text-halo-color": "#020710",
        "text-halo-width": 1.5,
      },
    },

    // 11. Major City Labels
    {
      id: "place-labels-city",
      type: "symbol",
      source: "openfreemap",
      "source-layer": "place",
      filter: ["in", "class", "city", "town"],
      minzoom: 4,
      layout: {
        "text-field": ["coalesce", ["get", "name_en"], ["get", "name"]],
        "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 4, 9, 10, 14],
        "text-max-width": 10,
      },
      paint: {
        "text-color": "#38bdf8",
        "text-halo-color": "#020710",
        "text-halo-width": 1.2,
      },
    },
  ],
};

/**
 * Generate a standalone offline-compatible style for PMTiles vector, raster, or terrain archives.
 */
export function createPMTilesStyle(
  pmtilesUrl: string,
  tileType: "vector" | "raster_png" | "raster_jpeg" | "raster_webp" | "raster_avif" | "raster-dem" | string = "vector",
  metadata?: { vector_layers?: Array<{ id: string }> } | null
): StyleSpecification {
  const isRaster = tileType.startsWith("raster_") || tileType === "raster";
  const isDem = tileType === "raster-dem" || tileType === "terrain";

  if (isDem) {
    return {
      version: 8,
      name: "Charlie Offline PMTiles Terrain",
      sources: {
        local_pmtiles_dem: {
          type: "raster-dem",
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
          id: "pmtiles-dem-hillshade",
          type: "hillshade",
          source: "local_pmtiles_dem",
          paint: {
            "hillshade-shadow-color": "#020710",
            "hillshade-highlight-color": "#22d3ee",
          },
        },
      ],
    };
  }

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

  // Vector archive: Adapt dynamically to discovered vector_layers metadata
  const vectorLayers: Array<{ id: string }> = metadata?.vector_layers && metadata.vector_layers.length > 0
    ? metadata.vector_layers
    : [
        { id: "water" },
        { id: "landuse" },
        { id: "transportation" },
        { id: "boundary" },
        { id: "places" },
      ];

  const layers: any[] = [
    {
      id: "background",
      type: "background",
      paint: {
        "background-color": "#020710",
      },
    },
  ];

  for (const vl of vectorLayers) {
    const layerName = vl.id.toLowerCase();
    if (layerName.includes("water") || layerName.includes("ocean")) {
      layers.push({
        id: `pmtiles-water-${vl.id}`,
        type: "fill",
        source: "local_pmtiles",
        "source-layer": vl.id,
        paint: {
          "fill-color": "#030a16",
        },
      });
    } else if (layerName.includes("land") || layerName.includes("building") || layerName.includes("structure")) {
      layers.push({
        id: `pmtiles-land-${vl.id}`,
        type: "fill",
        source: "local_pmtiles",
        "source-layer": vl.id,
        paint: {
          "fill-color": "#061422",
        },
      });
    } else if (layerName.includes("road") || layerName.includes("transport") || layerName.includes("street")) {
      layers.push({
        id: `pmtiles-roads-${vl.id}`,
        type: "line",
        source: "local_pmtiles",
        "source-layer": vl.id,
        paint: {
          "line-color": "rgba(34, 211, 238, 0.45)",
          "line-width": 1.2,
        },
      });
    } else if (layerName.includes("bound") || layerName.includes("admin")) {
      layers.push({
        id: `pmtiles-bounds-${vl.id}`,
        type: "line",
        source: "local_pmtiles",
        "source-layer": vl.id,
        paint: {
          "line-color": "rgba(34, 211, 238, 0.35)",
          "line-dasharray": [2, 2],
          "line-width": 1,
        },
      });
    } else {
      // Generic feature representation
      layers.push({
        id: `pmtiles-feat-line-${vl.id}`,
        type: "line",
        source: "local_pmtiles",
        "source-layer": vl.id,
        paint: {
          "line-color": "rgba(34, 211, 238, 0.35)",
          "line-width": 1,
        },
      });
      layers.push({
        id: `pmtiles-feat-circle-${vl.id}`,
        type: "circle",
        source: "local_pmtiles",
        "source-layer": vl.id,
        paint: {
          "circle-radius": 3,
          "circle-color": "#22d3ee",
          "circle-stroke-width": 1,
          "circle-stroke-color": "#020710",
        },
      });
    }
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
    layers,
  };
}
