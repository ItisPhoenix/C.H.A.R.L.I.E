import type { MapFeature } from "../types";

export interface LayerFetchResult {
  status: string;
  features: MapFeature[];
  attribution?: string;
  timestamp?: number;
  error?: string;
}

/**
 * Normalized backend intelligence layer fetcher.
 */
export async function fetchBackendIntelligenceLayer(
  layerId: string,
  signal?: AbortSignal
): Promise<LayerFetchResult> {
  try {
    const resp = await fetch(`/api/geo/layer/${encodeURIComponent(layerId)}`, { signal });
    if (!resp.ok) {
      throw new Error(`Layer endpoint responded with status ${resp.status}`);
    }

    const data = await resp.json();
    if (data.status === "error") {
      throw new Error(data.error || "Layer upstream error");
    }

    return {
      status: data.status || "ready",
      features: (data.features || []) as MapFeature[],
      attribution: data.attribution,
      timestamp: data.timestamp,
    };
  } catch (err) {
    if (signal?.aborted) {
      return { status: "idle", features: [] };
    }
    console.warn(`[IntelligenceProvider] Fetch failed for layer '${layerId}':`, err);
    throw err;
  }
}
