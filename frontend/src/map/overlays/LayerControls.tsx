import { useState, type ReactElement } from "react";
import { INTELLIGENCE_LAYERS } from "../layers/registry";
import { useMapStore } from "../mapStore";
import type { IntelligenceLayerDefinition } from "../types";

export function LayerControls(): ReactElement {
  const activeLayers = useMapStore((s) => s.activeLayers);
  const layerStatus = useMapStore((s) => s.layerStatus);
  const layerData = useMapStore((s) => s.layerData);
  const toggleLayer = useMapStore((s) => s.toggleLayer);

  const [isOpen, setIsOpen] = useState(false);
  const [filterQuery, setFilterQuery] = useState("");

  const activeCount = Object.values(activeLayers).filter(Boolean).length;

  // Group layers by category dynamically
  const layersByCategory = INTELLIGENCE_LAYERS.reduce<Record<string, IntelligenceLayerDefinition[]>>(
    (acc, layer) => {
      const cat = layer.category;
      if (!acc[cat]) acc[cat] = [];
      acc[cat].push(layer);
      return acc;
    },
    {}
  );

  return (
    <div className="absolute top-16 right-6 z-30 flex flex-col items-end pointer-events-auto font-mono">
      {/* Trigger Button */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border backdrop-blur-md transition cursor-pointer text-xs shadow-lg ${
          isOpen || activeCount > 0
            ? "bg-cyan-950/80 border-cyan-400/50 text-cyan-200 shadow-cyan-950/40"
            : "bg-slate-950/70 border-cyan-500/20 text-slate-300 hover:border-cyan-400/40 hover:text-cyan-300"
        }`}
        aria-label="Toggle intelligence layers menu"
      >
        <span
          className={`w-2 h-2 rounded-full ${
            activeCount > 0 ? "bg-cyan-400 animate-pulse" : "bg-slate-600"
          }`}
        />
        <span className="font-bold tracking-wider uppercase">LAYERS</span>
        <span className="text-[10px] px-1.5 py-0.2 rounded bg-slate-900 border border-cyan-500/20 text-cyan-300">
          {activeCount}
        </span>
      </button>

      {/* Slide-out Layer Controls Panel */}
      {isOpen && (
        <div className="mt-2 w-72 sm:w-80 p-3.5 rounded-xl border border-cyan-500/30 bg-slate-950/90 backdrop-blur-xl shadow-2xl text-left space-y-3 max-h-[70vh] overflow-y-auto">
          <div className="flex items-center justify-between border-b border-cyan-500/15 pb-2">
            <div>
              <div className="text-[10px] text-cyan-400 font-bold uppercase tracking-wider">
                INTELLIGENCE LAYERS
              </div>
              <div className="text-[9.5px] text-slate-400">Zero default load policy</div>
            </div>
            <button
              type="button"
              onClick={() => setIsOpen(false)}
              className="text-slate-400 hover:text-cyan-200 text-xs cursor-pointer p-1"
            >
              ✕
            </button>
          </div>

          {/* Quick Search */}
          <input
            type="text"
            placeholder="Filter feeds..."
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            className="w-full px-2.5 py-1 text-xs rounded bg-slate-900/80 border border-cyan-500/20 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-400/50"
          />

          {/* Grouped Layer Toggles */}
          <div className="space-y-3 pt-1">
            {Object.entries(layersByCategory).map(([category, layers]) => {
              const visibleLayers = layers.filter(
                (l) =>
                  !filterQuery ||
                  l.label.toLowerCase().includes(filterQuery.toLowerCase()) ||
                  l.attribution.toLowerCase().includes(filterQuery.toLowerCase())
              );

              if (visibleLayers.length === 0) return null;

              return (
                <div key={category} className="space-y-1.5">
                  <div className="text-[9.5px] font-bold text-cyan-400/70 uppercase tracking-wider">
                    {category.toUpperCase()}
                  </div>

                  <div className="space-y-1">
                    {visibleLayers.map((layer) => {
                      const isActive = Boolean(activeLayers[layer.id]);
                      const status = layerStatus[layer.id];
                      const isLoading = status?.status === "loading";
                      const count = layerData[layer.id]?.length;

                      return (
                        <div
                          key={layer.id}
                          onClick={() => toggleLayer(layer.id)}
                          className={`p-2 rounded-lg border transition cursor-pointer flex items-center justify-between gap-2 ${
                            isActive
                              ? "bg-cyan-950/60 border-cyan-400/40 text-slate-100"
                              : "bg-slate-900/40 border-cyan-500/10 text-slate-400 hover:border-cyan-500/30 hover:text-slate-200"
                          }`}
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <span
                              className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                              style={{
                                backgroundColor: isActive ? "#00f0ff" : "#475569",
                              }}
                            />
                            <div className="min-w-0">
                              <div className="text-xs font-medium truncate">{layer.label}</div>
                              <div className="text-[9px] text-slate-500 truncate">{layer.attribution}</div>
                            </div>
                          </div>

                          <div className="flex items-center gap-1.5 flex-shrink-0">
                            {isLoading && (
                              <span className="w-2 h-2 rounded-full bg-cyan-400 animate-ping" />
                            )}
                            {count !== undefined && isActive && (
                              <span className="text-[9.5px] px-1.5 py-0.2 rounded bg-cyan-950 border border-cyan-500/30 text-cyan-300">
                                {count}
                              </span>
                            )}
                            <input
                              type="checkbox"
                              checked={isActive}
                              onChange={() => {}} // handled by row onClick
                              className="w-3.5 h-3.5 accent-cyan-400 cursor-pointer"
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
