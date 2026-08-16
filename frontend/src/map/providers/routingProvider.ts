import type { MapRoute } from "../types";

export interface RouteRequest {
  start: [number, number];
  destination: [number, number];
  startLabel?: string;
  destinationLabel?: string;
  mode?: "driving" | "walking" | "transit";
}

/**
 * Fetch vehicular / corridor route via Charlie backend endpoint (/api/geo/route).
 */
export async function fetchRoute(
  req: RouteRequest,
  signal?: AbortSignal
): Promise<MapRoute | null> {
  const [startLon, startLat] = req.start;
  const [destLon, destLat] = req.destination;
  const startLabel = req.startLabel || "Origin";
  const destLabel = req.destinationLabel || "Destination";
  const mode = req.mode || "driving";

  try {
    const params = new URLSearchParams({
      start_lon: startLon.toString(),
      start_lat: startLat.toString(),
      dest_lon: destLon.toString(),
      dest_lat: destLat.toString(),
      start_label: startLabel,
      dest_label: destLabel,
      mode,
    });

    const resp = await fetch(`/api/geo/route?${params.toString()}`, { signal });
    if (!resp.ok) {
      throw new Error(`Routing endpoint failed with status ${resp.status}`);
    }

    const data = await resp.json();
    return {
      start: req.start,
      startLabel,
      destination: req.destination,
      destinationLabel: destLabel,
      geometry: data.geometry,
      distanceKm: data.distanceKm,
      durationMin: data.durationMin,
      steps: data.steps,
      mode,
      provider: data.provider || "osrm",
    };
  } catch (err) {
    if (signal?.aborted) return null;
    console.warn("[RoutingProvider] Route fetch error:", err);
    return null;
  }
}
