import type { MapFeature } from "../types";

export interface GeocodingResult {
  name: string;
  display_name: string;
  coordinates: [number, number]; // [lon, lat]
  bounding_box?: [[number, number], [number, number]];
  category?: string;
  place_type?: string;
  provider: string;
}

/**
 * Geocode query via Charlie backend provider endpoint (/api/geo/geocode).
 */
export async function geocodeLocation(
  query: string,
  signal?: AbortSignal
): Promise<GeocodingResult[]> {
  const trimmed = query.trim();
  if (!trimmed) return [];

  try {
    const url = `/api/geo/geocode?q=${encodeURIComponent(trimmed)}&limit=5`;
    const resp = await fetch(url, { signal });
    if (!resp.ok) {
      throw new Error(`Geocoding failed with status ${resp.status}`);
    }

    const data = await resp.json();
    return (data.results || []) as GeocodingResult[];
  } catch (err) {
    if (signal?.aborted) return [];
    console.warn(`[GeocodingProvider] Error resolving "${query}":`, err);
    return [];
  }
}

/**
 * Convert geocoded result to a selectable MapFeature.
 */
export function geocodingResultToFeature(res: GeocodingResult): MapFeature {
  return {
    id: `geo_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
    label: res.name,
    description: res.display_name,
    coordinates: res.coordinates,
    category: res.category || res.place_type || "Location",
    severity: "normal",
    source: `Charlie Geo (${res.provider})`,
    color: "#00f0ff",
  };
}
