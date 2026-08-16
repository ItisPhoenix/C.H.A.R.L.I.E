import React, { Suspense, type ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";
import type { SpatialMapData } from "./SpatialMapTypes";
import { SpatialMapFallback } from "./SpatialMapFallback";

export type { SpatialMapData, SpatialMapNode, SpatialMapEdge, SpatialMapLayer } from "./SpatialMapTypes";

// Lazy-load MapEngine dynamically so idle SurfaceComposer does NOT pull MapLibre / Deck.gl into main bundle
const LazyMapEngine = React.lazy(() =>
  import("../../map/MapEngine").then((m) => ({ default: m.MapEngine }))
);

export function SpatialMapPrimitive({
  primitive,
  data,
}: {
  primitive?: PrimitiveSpec;
  data?: SpatialMapData;
}): ReactElement {
  const mapData: SpatialMapData = data || primitive?.data || {};
  const mode = mapData.mode || "geo";

  // In production geo mode with no explicit override, lazy-mount the real MapEngine
  if (mode === "geo" && mapData.useRealEngine !== false) {
    return (
      <div className="w-full h-full min-h-[300px] relative rounded-xl overflow-hidden border border-cyan-500/20 shadow-xl">
        <Suspense
          fallback={
            <div className="w-full h-full min-h-[300px] flex items-center justify-center bg-[#020710] font-mono text-cyan-400 text-xs">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-cyan-400 animate-ping" />
                <span>LOADING SPATIAL ENGINE...</span>
              </div>
            </div>
          }
        >
          <LazyMapEngine />
        </Suspense>
      </div>
    );
  }

  return <SpatialMapFallback data={mapData} />;
}
