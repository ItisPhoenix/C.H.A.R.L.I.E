import type { MapFeature } from "../types";

/**
 * Normalized backend intelligence layer fetcher.
 */
export async function fetchBackendIntelligenceLayer(
  layerId: string,
  signal?: AbortSignal
): Promise<MapFeature[]> {
  try {
    const resp = await fetch(`/api/geo/layer/${encodeURIComponent(layerId)}`, { signal });
    if (!resp.ok) {
      throw new Error(`Layer endpoint responded with status ${resp.status}`);
    }

    const data = await resp.json();
    if (data.status === "error") {
      throw new Error(data.error || "Layer upstream error");
    }

    return (data.features || []) as MapFeature[];
  } catch (err) {
    if (signal?.aborted) return [];
    console.warn(`[IntelligenceProvider] Fetch failed for layer '${layerId}':`, err);
    throw err;
  }
}
