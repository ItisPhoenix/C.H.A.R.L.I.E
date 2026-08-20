import type { StyleSpecification } from "maplibre-gl";

/**
 * C.H.A.R.L.I.E. Dark Spatial Intelligence Map Style
 *
 * Visual hierarchy (brightest → dimmest):
 *   active intelligence / route overlay
 *   > luminous city / network hub nodes
 *   > major network corridors (motorway/trunk) at regional zoom
 *   > primary roads at city zoom
 *   > minor roads (city zoom only)
 *   > administrative boundaries (faint at world, more visible at regional)
 *   > base geography (water / land)
 *
 * Zoom vocabulary:
 *   World    z0–3   : only country outlines + sparse labels
 *   Regional z4–7   : state boundaries appear, major corridors brighten
 *   City     z8–14  : roads fill in, buildings extrude
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
    // Independent country geometry fallback. This is a real, low-resolution
    // GeoJSON geography layer so the map remains recognizable if a vector or
    // raster tile provider is unavailable.
    world_countries_fallback: {
      type: "geojson",
      data: "https://raw.githubusercontent.com/johan/world.geo.json/master/countries.geo.json",
      attribution: "© Natural Earth / world.geo.json contributors",
    },
  },
  glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
  layers: [
    // 1. Deep space technical base
    {
      id: "background",
      type: "background",
      paint: {
        "background-color": "#040a17",
      },
    },

    // 2. High-speed raster foundation — provides visible continent landmass / oceans
    {
      id: "carto-dark-base",
      type: "raster",
      source: "carto_dark_raster",
      minzoom: 0,
      maxzoom: 19,
      paint: {
        "raster-opacity": 0.95,
      },
    },

    // Real country silhouettes and borders, intentionally restrained so they
    // read as geography rather than a fabricated intelligence overlay.
    {
      id: "world-countries-fallback-fill",
      type: "fill",
      source: "world_countries_fallback",
      paint: {
        "fill-color": "#0a1b2c",
        "fill-opacity": 0.9,
      },
    },
    {
      id: "world-countries-fallback-border",
      type: "line",
      source: "world_countries_fallback",
      paint: {
        "line-color": "rgba(77, 183, 226, 0.42)",
        "line-width": ["interpolate", ["linear"], ["zoom"], 0, 0.45, 5, 0.8, 10, 1.1],
      },
    },

    // 3. Vector Water — near-black, crisp separation
    {
      id: "water-vector",
      type: "fill",
      source: "openfreemap",
      "source-layer": "water",
      paint: {
        "fill-color": "#020b18",
        "fill-opacity": 0.95,
      },
    },

    // 4. Vector Landcover — subtle texture overlay
    {
      id: "landcover-vector",
      type: "fill",
      source: "openfreemap",
      "source-layer": "landcover",
      paint: {
        "fill-color": "#081526",
        "fill-opacity": 0.5,
      },
    },

    // 5. Administrative Country Boundaries — crisp technical dashed lines
    {
      id: "boundaries-country",
      type: "line",
      source: "openfreemap",
      "source-layer": "boundary",
      filter: ["==", "admin_level", 2],
      paint: {
        "line-color": [
          "interpolate", ["linear"], ["zoom"],
          0, "rgba(56, 189, 248, 0.35)",
          4, "rgba(56, 189, 248, 0.50)",
          8, "rgba(56, 189, 248, 0.60)",
        ],
        "line-width": [
          "interpolate", ["linear"], ["zoom"],
          0, 0.8,
          4, 1.2,
          8, 1.5,
        ],
        "line-dasharray": [4, 3],
      },
    },

    // 6. State / Regional Boundaries
    {
      id: "boundaries-state",
      type: "line",
      source: "openfreemap",
      "source-layer": "boundary",
      filter: [">", "admin_level", 2],
      minzoom: 4,
      paint: {
        "line-color": [
          "interpolate", ["linear"], ["zoom"],
          4, "rgba(56, 189, 248, 0.15)",
          7, "rgba(56, 189, 248, 0.28)",
          10, "rgba(56, 189, 248, 0.35)",
        ],
        "line-width": [
          "interpolate", ["linear"], ["zoom"],
          4, 0.5,
          8, 0.9,
        ],
        "line-dasharray": [3, 3],
      },
    },

    // 7. Major Network Corridors — Motorways and Trunk roads (Muted Cyan Corridor)
    {
      id: "roads-motorway",
      type: "line",
      source: "openfreemap",
      "source-layer": "transportation",
      filter: ["in", "class", "motorway", "trunk"],
      minzoom: 4,
      paint: {
        "line-color": [
          "interpolate", ["linear"], ["zoom"],
          4, "rgba(0, 180, 216, 0.28)",
          7, "rgba(0, 180, 216, 0.45)",
          12, "rgba(0, 180, 216, 0.60)",
        ],
        "line-width": [
          "interpolate", ["linear"], ["zoom"],
          4, 0.8,
          8, 1.6,
          12, 2.8,
          16, 4.2,
        ],
      },
    },

    // 8. Primary Arterial Roads — Deep slate-blue (distinctly dimmer than active route)
    {
      id: "roads-primary",
      type: "line",
      source: "openfreemap",
      "source-layer": "transportation",
      filter: ["in", "class", "primary"],
      minzoom: 6,
      paint: {
        "line-color": [
          "interpolate", ["linear"], ["zoom"],
          6, "rgba(30, 120, 180, 0.18)",
          9, "rgba(30, 120, 180, 0.32)",
          13, "rgba(30, 120, 180, 0.45)",
        ],
        "line-width": [
          "interpolate", ["linear"], ["zoom"],
          6, 0.5,
          10, 1.4,
          15, 2.8,
        ],
      },
    },

    // 9. Secondary / Tertiary Roads — Dark muted navy (subdued background fabric)
    {
      id: "roads-secondary",
      type: "line",
      source: "openfreemap",
      "source-layer": "transportation",
      filter: ["in", "class", "secondary", "tertiary"],
      minzoom: 9,
      paint: {
        "line-color": [
          "interpolate", ["linear"], ["zoom"],
          9, "rgba(15, 60, 110, 0.12)",
          13, "rgba(15, 60, 110, 0.24)",
        ],
        "line-width": [
          "interpolate", ["linear"], ["zoom"],
          9, 0.4,
          14, 1.2,
        ],
      },
    },

    // 10. Minor Roads — deepest zoom only, ultra-faint
    {
      id: "roads-minor",
      type: "line",
      source: "openfreemap",
      "source-layer": "transportation",
      filter: ["!in", "class", "motorway", "trunk", "primary", "secondary", "tertiary"],
      minzoom: 12,
      paint: {
        "line-color": "rgba(15, 50, 90, 0.10)",
        "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.4, 16, 1.0],
      },
    },

    // 11. 3D Extruded Buildings (city zoom + pitched)
    {
      id: "building-3d",
      type: "fill-extrusion",
      source: "openfreemap",
      "source-layer": "building",
      minzoom: 14,
      paint: {
        "fill-extrusion-color": "#0c2844",
        "fill-extrusion-height": ["coalesce", ["get", "render_height"], 15],
        "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
        "fill-extrusion-opacity": 0.85,
      },
    },

    // 12. Luminous city / network hub nodes — subtle glow at world/regional zoom
    //     Appear as faint pulsing hubs at z3-7, grow detail at city zoom
    {
      id: "place-hub-circles",
      type: "circle",
      source: "openfreemap",
      "source-layer": "place",
      filter: ["in", "class", "city", "capital"],
      minzoom: 2,
      maxzoom: 9,
      paint: {
        "circle-radius": [
          "interpolate", ["linear"], ["zoom"],
          2, 1.5,
          5, 3.0,
          8, 4.5,
        ],
        "circle-color": "rgba(34, 211, 238, 0.55)",
        "circle-blur": [
          "interpolate", ["linear"], ["zoom"],
          2, 1.2,
          6, 0.6,
        ],
        "circle-opacity": [
          "interpolate", ["linear"], ["zoom"],
          2, 0.4,
          5, 0.7,
          8, 0.0,
        ],
      },
    },

    // 13. Country & State Place Labels — restrained, zoom-appropriate
    {
      id: "place-labels-country",
      type: "symbol",
      source: "openfreemap",
      "source-layer": "place",
      filter: ["in", "class", "country", "state"],
      layout: {
        "text-field": ["coalesce", ["get", "name_en"], ["get", "name"]],
        "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 1, 8.0, 4, 11.0, 7, 13.5],
        "text-transform": "uppercase",
        "text-letter-spacing": 0.12,
        "text-max-width": 8,
      },
      paint: {
        "text-color": [
          "interpolate", ["linear"], ["zoom"],
          0, "rgba(148, 163, 184, 0.65)",
          4, "rgba(203, 213, 225, 0.90)",
        ],
        "text-halo-color": "#020710",
        "text-halo-width": 2.0,
        "text-opacity": [
          "interpolate", ["linear"], ["zoom"],
          0, 0.6,
          3, 1.0,
        ],
      },
    },

    // 14. Major City Labels — sparse, zoom-appropriate brightness
    {
      id: "place-labels-city",
      type: "symbol",
      source: "openfreemap",
      "source-layer": "place",
      filter: ["in", "class", "city", "town"],
      minzoom: 3,
      layout: {
        "text-field": ["coalesce", ["get", "name_en"], ["get", "name"]],
        "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
        "text-size": [
          "interpolate", ["linear"], ["zoom"],
          3, 8.5,
          6, 11.5,
          10, 14.5,
        ],
        "text-max-width": 10,
      },
      paint: {
        "text-color": [
          "interpolate", ["linear"], ["zoom"],
          3, "rgba(125, 211, 252, 0.65)",
          7, "rgba(125, 211, 252, 0.90)",
        ],
        "text-halo-color": "#020710",
        "text-halo-width": 1.5,
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
