import { useState, type ReactElement } from "react";
import type { SpatialMapData, SpatialMapNode, SpatialMapEdge, SpatialMapLayer } from "./SpatialMapTypes";

/**
 * Natural Earth Derived Precision World Geographic Vector Contours (1000 x 500 Equirectangular Projection)
 * Maintained as an emergency WebGL-unavailable and deterministic unit-test fallback.
 */
const WORLD_LANDMASS_PATHS = [
  // North America
  "M 45 75 C 60 55 90 42 125 38 C 160 35 200 40 230 52 C 248 60 262 50 282 65 C 300 80 305 105 295 125 C 280 135 260 130 252 145 C 265 158 260 178 248 198 C 245 220 238 238 226 250 C 230 265 222 272 208 260 C 196 250 178 260 165 282 C 150 302 140 290 135 272 C 125 248 115 225 98 212 C 80 192 65 168 48 142 C 35 115 32 90 45 75 Z",
  // Greenland
  "M 315 28 C 338 22 358 32 364 52 C 362 75 345 88 328 80 C 315 72 310 50 315 28 Z",
  // South America
  "M 175 285 C 200 275 230 288 262 312 C 285 340 290 370 278 408 C 260 445 238 478 222 485 C 210 460 202 418 192 372 C 180 332 165 308 175 285 Z",
  // Europe
  "M 430 75 C 448 60 472 68 480 90 C 475 115 458 132 444 122 C 432 98 426 84 430 75 Z",
  "M 385 102 C 398 95 404 112 396 125 C 385 124 382 110 385 102 Z",
  "M 390 165 C 415 160 415 192 388 192 C 382 178 385 168 390 165 Z",
  "M 415 130 C 442 128 465 138 468 160 C 450 170 430 166 415 154 C 410 142 412 134 415 130 Z",
  // Africa
  "M 405 190 C 470 184 522 225 518 285 C 495 355 465 412 435 432 C 415 390 395 332 382 272 C 385 220 395 200 405 190 Z",
  // Eurasia / Asia
  "M 485 80 C 560 60 665 52 750 55 C 820 75 835 115 812 138 C 775 158 732 188 675 210 C 615 220 545 195 505 145 C 485 110 480 92 485 80 Z",
  "M 575 230 C 618 225 610 288 585 282 C 572 260 572 240 575 230 Z",
  "M 715 188 C 770 200 750 252 710 280 C 670 272 665 248 715 188 Z",
  "M 805 135 C 824 142 820 172 802 165 C 800 148 802 140 805 135 Z",
  // Oceania
  "M 690 315 C 772 310 795 355 770 410 C 715 408 675 370 680 335 C 682 322 685 318 690 315 Z",
  "M 815 392 C 825 395 820 422 812 418 C 812 402 812 396 815 392 Z",
];

export function SpatialMapFallback({ data }: { data?: SpatialMapData }): ReactElement {
  const mapData: SpatialMapData = data || {};
  const mode = mapData.mode || "geo";
  const title = mapData.title;
  const subtitle = mapData.subtitle || "SPATIAL FEED";

  const rawNodes: SpatialMapNode[] = Array.isArray(mapData.nodes) ? [...mapData.nodes] : [];
  const rawEdges: SpatialMapEdge[] = Array.isArray(mapData.edges) ? [...mapData.edges] : [];
  const initialLayers: SpatialMapLayer[] = Array.isArray(mapData.layers) ? mapData.layers : [];

  // If radar objects are provided without explicit nodes, synthesize meaningful topology network
  if (rawNodes.length === 0 && (mapData.radar?.objects || (mapData as any).objects)) {
    const radarObjects = mapData.radar?.objects || (mapData as any).objects || [];
    const center = [500, 250];
    const maxRadius = 210;
    radarObjects.forEach((obj: any, idx: number) => {
      const rad = ((obj.angle ?? (idx * 360) / Math.max(1, radarObjects.length)) - 90) * (Math.PI / 180);
      const dist = (obj.distance ?? 0.6) * maxRadius;
      const nx = Math.max(80, Math.min(920, center[0] + Math.cos(rad) * dist));
      const ny = Math.max(60, Math.min(440, center[1] + Math.sin(rad) * dist));
      rawNodes.push({
        id: obj.id || `node_${idx}`,
        label: obj.label || `NODE_${idx}`,
        sublabel: obj.type ? `${String(obj.type).toUpperCase()} UPLINK` : undefined,
        x: (nx / 1000) * 100,
        y: (ny / 500) * 100,
        status: obj.status || "active",
        color: obj.type === "hub" ? "#22d3ee" : obj.type === "signal" ? "#38bdf8" : "#818cf8",
      });
    });

    // Synthesize fiber corridor links between hubs and gateways
    for (let i = 0; i < rawNodes.length - 1; i++) {
      rawEdges.push({
        from: rawNodes[i].id,
        to: rawNodes[i + 1].id,
        type: "route",
        active: true,
      });
    }
    if (rawNodes.length > 2) {
      rawEdges.push({
        from: rawNodes[rawNodes.length - 1].id,
        to: rawNodes[0].id,
        type: "route",
        active: true,
      });
    }
  }

  const [activeLayers, setActiveLayers] = useState<Record<string, boolean>>(() =>
    initialLayers.reduce((acc, l) => ({ ...acc, [l.id]: l.defaultActive ?? true }), {})
  );

  const toggleLayer = (layerId: string) => {
    setActiveLayers((prev) => ({ ...prev, [layerId]: !prev[layerId] }));
  };

  const nodeMap = new Map<string, SpatialMapNode>(rawNodes.map((n) => [n.id, n]));

  return (
    <div className="w-full h-full relative flex flex-col justify-between overflow-hidden select-none font-mono">
      {(title || subtitle) && (
        <div className="flex items-center justify-between mb-2">
          <div>
            {title && (
              <div className="text-xs font-bold text-cyan-200 tracking-wider uppercase">
                {title}
              </div>
            )}
            {subtitle && (
              <div className="text-[10px] text-cyan-400/70 tracking-wider uppercase mt-0.5">
                {subtitle}
              </div>
            )}
          </div>
        </div>
      )}

      {/* SVG Canvas */}
      <div className="flex-1 w-full min-h-[280px] relative rounded-2xl border border-cyan-500/20 bg-slate-950/70 backdrop-blur-md overflow-hidden shadow-2xl">
        <svg viewBox="0 0 1000 500" className="w-full h-full" preserveAspectRatio="xMidYMid meet">
          <defs>
            {/* Focused circular radar glow — restrained, topology remains focal data */}
            <radialGradient id="map-radar-glow" cx="500" cy="250" r="240" gradientUnits="userSpaceOnUse">
              <stop offset="0%" stopColor="#00f0ff" stopOpacity="0.06" />
              <stop offset="60%" stopColor="#00f0ff" stopOpacity="0.01" />
              <stop offset="100%" stopColor="#000000" stopOpacity="0.0" />
            </radialGradient>
            <filter id="glow-cyan" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
          </defs>

          {/* Radar background glow — focused circle centered on radar coordinates */}
          <rect width="1000" height="500" fill="url(#map-radar-glow)" />

          {/* Coordinate Technical Grids */}
          <g stroke="rgba(0, 240, 255, 0.08)" strokeWidth="0.8" strokeDasharray="3 3">
            {[100, 200, 300, 400, 500, 600, 700, 800, 900].map((x) => (
              <line key={`gx-${x}`} x1={x} y1="0" x2={x} y2="500" />
            ))}
            {[100, 200, 300, 400].map((y) => (
              <line key={`gy-${y}`} x1="0" y1={y} x2="1000" y2={y} />
            ))}
          </g>

          {/* Concentric Radar Rings in Radar Mode — fainter, tighter radius */}
          {mode === "radar" && (
            <g stroke="rgba(0, 240, 255, 0.08)" strokeWidth="0.8" fill="none">
              <circle cx="500" cy="250" r="80" />
              <circle cx="500" cy="250" r="150" strokeDasharray="4 4" />
              <circle cx="500" cy="250" r="180" stroke="rgba(0, 240, 255, 0.12)" />
              <line x1="500" y1="70" x2="500" y2="430" strokeDasharray="2 5" stroke="rgba(0,240,255,0.06)" />
              <line x1="320" y1="250" x2="680" y2="250" strokeDasharray="2 5" stroke="rgba(0,240,255,0.06)" />
            </g>
          )}

          {/* Geographic World Contours */}
          {(mode === "geo" || mode === "generic_spatial") && (
            <g>
              {WORLD_LANDMASS_PATHS.map((d, i) => (
                <path
                  key={`land-${i}`}
                  d={d}
                  fill="#061422"
                  stroke="#00f0ff"
                  strokeWidth="1.0"
                  strokeOpacity="0.45"
                  className="transition-all duration-500 hover:fill-[#0a233c] hover:stroke-cyan-300"
                />
              ))}
            </g>
          )}

          {/* Edges / Network Topology Links */}
          <g>
            {rawEdges.map((edge, i) => {
              const src = nodeMap.get(edge.from);
              const dst = nodeMap.get(edge.to);
              if (!src || !dst) return null;

              const x1 = (src.x / 100) * 1000;
              const y1 = (src.y / 100) * 500;
              const x2 = (dst.x / 100) * 1000;
              const y2 = (dst.y / 100) * 500;

              return (
                <g key={`edge-${i}`}>
                  <line
                    x1={x1}
                    y1={y1}
                    x2={x2}
                    y2={y2}
                    stroke={edge.active !== false ? "#00f0ff" : "rgba(34, 211, 238, 0.25)"}
                    strokeWidth={edge.type === "route" ? "2.5" : "1.2"}
                    strokeDasharray={edge.type === "dotted" ? "4 4" : undefined}
                    strokeOpacity={edge.active !== false ? "0.8" : "0.3"}
                  />
                  {edge.type === "route" && (
                    <circle cx={(x1 + x2) / 2} cy={(y1 + y2) / 2} r="3" fill="#00f0ff" className="animate-ping" />
                  )}
                </g>
              );
            })}
          </g>

          {/* Nodes / Points of Interest */}
          <g>
            {rawNodes.map((node) => {
              const cx = (node.x / 100) * 1000;
              const cy = (node.y / 100) * 500;
              const color = node.color || (node.status === "warning" ? "#fbbf24" : node.status === "error" ? "#f43f5e" : "#00f0ff");

              return (
                <g key={node.id} className="cursor-pointer group">
                  <circle cx={cx} cy={cy} r="10" fill={color} fillOpacity="0.15" className="animate-pulse" />
                  <circle cx={cx} cy={cy} r="4.5" fill={color} stroke="#020710" strokeWidth="1.5" />
                  <text
                    x={cx}
                    y={cy - 10}
                    textAnchor="middle"
                    fill="#e2e8f0"
                    fontSize="10"
                    fontWeight="bold"
                    fontFamily="monospace"
                    className="drop-shadow-md"
                  >
                    {node.label}
                  </text>
                  {node.sublabel && (
                    <text
                      x={cx}
                      y={cy + 16}
                      textAnchor="middle"
                      fill="#94a3b8"
                      fontSize="8"
                      fontFamily="monospace"
                    >
                      {node.sublabel}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      {/* Layer Toggles & Metrics Panel */}
      {initialLayers.length > 0 && (
        <div className="flex items-center gap-2 mt-2 pt-2 border-t border-cyan-500/15 overflow-x-auto text-[10px]">
          <span className="text-slate-500 uppercase tracking-widest font-mono text-[9px]">Layers:</span>
          {initialLayers.map((layer) => {
            const active = !!activeLayers[layer.id];
            return (
              <button
                key={layer.id}
                type="button"
                onClick={() => toggleLayer(layer.id)}
                className={`px-2 py-0.5 rounded transition cursor-pointer font-mono border ${
                  active
                    ? "bg-cyan-950/80 text-cyan-300 border-cyan-400/50 shadow-sm shadow-cyan-500/20"
                    : "bg-slate-900/60 text-slate-500 border-slate-700/40 hover:text-slate-300"
                }`}
              >
                {layer.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
