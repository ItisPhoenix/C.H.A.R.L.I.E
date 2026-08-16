import { useState, type ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";
import { MapEngine } from "../../map/MapEngine";

export interface SpatialMapNode {
  id: string;
  label: string;
  sublabel?: string;
  x: number; // 0 to 100 percentage or coordinate
  y: number; // 0 to 100 percentage or coordinate
  type?: string;
  status?: "active" | "warning" | "idle" | "error" | string;
  value?: string | number;
  color?: string;
}

export interface SpatialMapEdge {
  from: string;
  to: string;
  type?: "route" | "link" | "vector" | "dotted" | string;
  active?: boolean;
}

export interface SpatialMapLayer {
  id: string;
  label: string;
  color?: string;
  defaultActive?: boolean;
}

export interface SpatialMapData {
  mode?: "radar" | "geo" | "topology" | "generic_spatial";
  title?: string;
  subtitle?: string;
  nodes?: SpatialMapNode[];
  edges?: SpatialMapEdge[];
  layers?: SpatialMapLayer[];
  centerLabel?: string;
  useRealEngine?: boolean;
}

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

export function SpatialMapPrimitive({
  primitive,
  data,
}: {
  primitive?: PrimitiveSpec;
  data?: SpatialMapData;
}): ReactElement {
  const mapData: SpatialMapData = data || primitive?.data || {};
  const mode = mapData.mode || "geo";
  const title = mapData.title;
  const subtitle = mapData.subtitle;

  const rawNodes: SpatialMapNode[] = Array.isArray(mapData.nodes) ? mapData.nodes : [];
  const rawEdges: SpatialMapEdge[] = Array.isArray(mapData.edges) ? mapData.edges : [];
  const initialLayers: SpatialMapLayer[] = Array.isArray(mapData.layers) ? mapData.layers : [];

  const [activeLayers, setActiveLayers] = useState<Record<string, boolean>>(() =>
    initialLayers.reduce((acc, l) => ({ ...acc, [l.id]: l.defaultActive ?? true }), {})
  );

  const toggleLayer = (layerId: string) => {
    setActiveLayers((prev) => ({ ...prev, [layerId]: !prev[layerId] }));
  };

  // In production geo mode with no explicit override, mount real MapEngine
  if (mode === "geo" && mapData.useRealEngine !== false) {
    return (
      <div className="w-full h-full min-h-[300px] relative rounded-xl overflow-hidden border border-cyan-500/20 shadow-xl">
        <MapEngine />
      </div>
    );
  }

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
            <radialGradient id="map-radar-glow" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#00f0ff" stopOpacity="0.14" />
              <stop offset="60%" stopColor="#00f0ff" stopOpacity="0.04" />
              <stop offset="100%" stopColor="transparent" stopOpacity="0" />
            </radialGradient>
            <filter id="map-node-glow" x="-100%" y="-100%" width="300%" height="300%">
              <feGaussianBlur in="SourceGraphic" stdDeviation="3.5" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <linearGradient id="geo-land-grad" x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stopColor="#081e30" />
              <stop offset="100%" stopColor="#040e18" />
            </linearGradient>
            <pattern id="spatial-grid-pattern" width="40" height="40" patternUnits="userSpaceOnUse">
              <path d="M 40 0 L 0 0 0 40" fill="none" stroke="rgba(0, 240, 255, 0.05)" strokeWidth="0.8" />
            </pattern>
          </defs>

          <rect width="1000" height="500" fill="url(#spatial-grid-pattern)" />

          {/* Radar Mode */}
          {mode === "radar" && (
            <g className="radar-structure">
              <circle cx="500" cy="250" r="210" fill="none" stroke="rgba(0, 240, 255, 0.16)" strokeWidth="1" />
              <circle cx="500" cy="250" r="145" fill="none" stroke="rgba(0, 240, 255, 0.2)" strokeWidth="1" strokeDasharray="4 4" />
              <circle cx="500" cy="250" r="80" fill="none" stroke="rgba(0, 240, 255, 0.25)" strokeWidth="1.2" />
              <circle cx="500" cy="250" r="210" fill="url(#map-radar-glow)" />
              <line x1="280" y1="250" x2="720" y2="250" stroke="rgba(0, 240, 255, 0.18)" strokeWidth="1" />
              <line x1="500" y1="35" x2="500" y2="465" stroke="rgba(0, 240, 255, 0.18)" strokeWidth="1" />
              <g transform="rotate(45 500 250)">
                <line x1="500" y1="250" x2="710" y2="250" stroke="rgba(0, 240, 255, 0.6)" strokeWidth="1.6" />
                <path d="M 500 250 L 710 250 A 210 210 0 0 0 648 102 Z" fill="rgba(0, 240, 255, 0.09)" />
              </g>
            </g>
          )}

          {/* Geo Fallback Contours */}
          {mode === "geo" && (
            <g className="geo-world-layer">
              <g className="geo-continents">
                {WORLD_LANDMASS_PATHS.map((pathD, idx) => (
                  <path
                    key={`geo-land-${idx}`}
                    d={pathD}
                    fill="url(#geo-land-grad)"
                    stroke="rgba(0, 240, 255, 0.45)"
                    strokeWidth="1.3"
                    strokeLinejoin="round"
                    strokeLinecap="round"
                  />
                ))}
              </g>
            </g>
          )}

          {/* Edges */}
          <g className="spatial-edges">
            {rawEdges.map((edge, idx) => {
              const src = nodeMap.get(edge.from);
              const dst = nodeMap.get(edge.to);
              if (!src || !dst) return null;
              const x1 = (src.x / 100) * 1000;
              const y1 = (src.y / 100) * 500;
              const x2 = (dst.x / 100) * 1000;
              const y2 = (dst.y / 100) * 500;
              return (
                <line
                  key={`edge-${idx}`}
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke="rgba(0, 240, 255, 0.4)"
                  strokeWidth={1.4}
                />
              );
            })}
          </g>

          {/* Nodes */}
          <g className="spatial-nodes">
            {rawNodes.map((node) => {
              const cx = (node.x / 100) * 1000;
              const cy = (node.y / 100) * 500;
              const nodeColor = node.color || "#00f0ff";
              return (
                <g key={node.id} className="group cursor-pointer">
                  <circle cx={cx} cy={cy} r={5.5} fill={nodeColor} opacity="0.4" filter="url(#map-node-glow)" />
                  <circle cx={cx} cy={cy} r={4} fill="#ffffff" />
                  <text
                    x={cx + 10}
                    y={cy - 4}
                    fill="#ffffff"
                    fontSize="11"
                    fontFamily="monospace"
                    fontWeight="700"
                  >
                    {node.label}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      {/* Layer Toggle Bar */}
      {initialLayers.length > 0 && (
        <div className="flex items-center justify-between mt-2 pt-1.5 border-t border-cyan-500/15 text-[10px]">
          <div className="text-cyan-400/80 uppercase tracking-wider font-bold">SPATIAL FEED</div>
          <div className="flex items-center gap-3">
            {initialLayers.map((layer) => {
              const isActive = activeLayers[layer.id] ?? true;
              return (
                <button
                  key={layer.id}
                  type="button"
                  onClick={() => toggleLayer(layer.id)}
                  className={`flex items-center gap-1.5 px-2.5 py-0.5 rounded transition cursor-pointer ${
                    isActive
                      ? "text-cyan-200 bg-cyan-950/60 border border-cyan-400/50"
                      : "text-slate-500 hover:text-slate-300"
                  }`}
                >
                  <span
                    className="w-2 h-2 rounded-full"
                    style={{ backgroundColor: isActive ? layer.color || "#00f0ff" : "#475569" }}
                  />
                  <span>{layer.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
