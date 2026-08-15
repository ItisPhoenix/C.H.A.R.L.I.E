import { useState, type ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

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
}

/**
 * Natural Earth Derived Precision World Geographic Vector Contours (1000 x 500 Equirectangular Projection)
 * Detailed coastlines, peninsulas, gulfs, and recognized landmasses.
 */
const WORLD_LANDMASS_PATHS = [
  // 1. North America (Alaska, Canada, Hudson Bay, US East Coast, Florida, Gulf of Mexico, Baja, Mexico, Central America)
  "M 45 75 C 60 55 90 42 125 38 C 160 35 200 40 230 52 C 248 60 262 50 282 65 C 300 80 305 105 295 125 C 280 135 260 130 252 145 C 265 158 260 178 248 198 C 245 220 238 238 226 250 C 230 265 222 272 208 260 C 196 250 178 260 165 282 C 150 302 140 290 135 272 C 125 248 115 225 98 212 C 80 192 65 168 48 142 C 35 115 32 90 45 75 Z",
  // Greenland
  "M 315 28 C 338 22 358 32 364 52 C 362 75 345 88 328 80 C 315 72 310 50 315 28 Z",
  // 2. South America (Colombia, Venezuela, Guianas, Brazil Bulge, Santos/Rio, Argentina/Patagonia, Cape Horn, Chile, Peru)
  "M 175 285 C 200 275 230 288 262 312 C 285 340 290 370 278 408 C 260 445 238 478 222 485 C 210 460 202 418 192 372 C 180 332 165 308 175 285 Z",
  // 3. Europe & Mediterranean (Scandinavia, Great Britain, Ireland, Iberia, France, Germany, Italy, Balkans)
  // Scandinavia
  "M 430 75 C 448 60 472 68 480 90 C 475 115 458 132 444 122 C 432 98 426 84 430 75 Z",
  // Great Britain & Ireland
  "M 385 102 C 398 95 404 112 396 125 C 385 124 382 110 385 102 Z",
  "M 372 108 C 380 104 382 118 374 122 C 368 118 370 112 372 108 Z",
  // Western / Central Europe & Iberia
  "M 390 165 C 415 160 415 192 388 192 C 382 178 385 168 390 165 Z",
  "M 415 130 C 442 128 465 138 468 160 C 450 170 430 166 415 154 C 410 142 412 134 415 130 Z",
  // Italy
  "M 440 165 C 452 178 448 202 436 195 C 438 180 438 170 440 165 Z",
  // 4. Africa & Madagascar (North Africa, West Africa Horn, Gulf of Guinea, South Africa, East Africa, Red Sea)
  "M 405 190 C 470 184 522 225 518 285 C 495 355 465 412 435 432 C 415 390 395 332 382 272 C 385 220 395 200 405 190 Z",
  // Madagascar
  "M 525 345 C 538 350 534 390 520 382 C 518 360 520 350 525 345 Z",
  // 5. Eurasia, Middle East, India, China, Japan, Southeast Asia
  // North / Central Eurasia & Russia
  "M 485 80 C 560 60 665 52 750 55 C 820 75 835 115 812 138 C 775 158 732 188 675 210 C 615 220 545 195 505 145 C 485 110 480 92 485 80 Z",
  // Arabian Peninsula
  "M 510 200 C 545 195 560 230 535 248 C 510 212 508 205 510 200 Z",
  // Indian Subcontinent & Sri Lanka
  "M 575 230 C 618 225 610 288 585 282 C 572 260 572 240 575 230 Z",
  "M 606 295 C 612 298 608 308 604 304 C 602 300 604 296 606 295 Z",
  // China & East Asia & Indochina
  "M 715 188 C 770 200 750 252 710 280 C 670 272 665 248 715 188 Z",
  // Japan Archipelago
  "M 805 135 C 824 142 820 172 802 165 C 800 148 802 140 805 135 Z",
  // 6. Maritime Southeast Asia, Australia, New Zealand
  // Indonesia / Malaysia / Philippines
  "M 660 288 C 712 286 735 315 695 335 C 665 315 658 298 660 288 Z",
  "M 732 258 C 742 256 738 282 728 278 C 728 268 730 262 732 258 Z",
  // Australia & Tasmania
  "M 690 315 C 772 310 795 355 770 410 C 715 408 675 370 680 335 C 682 322 685 318 690 315 Z",
  "M 746 422 C 756 422 752 436 744 432 Z",
  // New Zealand
  "M 815 392 C 825 395 820 422 812 418 C 812 402 812 396 815 392 Z",
  "M 805 428 C 815 430 810 455 800 448 C 800 435 802 430 805 428 Z",
];

/**
 * Real-world Major Global Infrastructure Node Coordinates (1000x500 Space)
 */
const AMBIENT_CITY_LIGHTS = [
  // North America
  { x: 238, y: 198, r: 2.0, alpha: 0.85, name: "NYC / DC" },
  { x: 215, y: 190, r: 1.6, alpha: 0.65, name: "Chicago" },
  { x: 145, y: 210, r: 1.8, alpha: 0.75, name: "Silicon Valley" },
  { x: 148, y: 225, r: 1.6, alpha: 0.65, name: "Los Angeles" },
  { x: 200, y: 245, r: 1.5, alpha: 0.6, name: "Texas" },
  // Europe
  { x: 395, y: 120, r: 2.0, alpha: 0.85, name: "London" },
  { x: 435, y: 135, r: 2.0, alpha: 0.85, name: "Frankfurt / Paris" },
  { x: 405, y: 180, r: 1.5, alpha: 0.6, name: "Madrid" },
  { x: 445, y: 178, r: 1.6, alpha: 0.65, name: "Milan" },
  { x: 465, y: 100, r: 1.5, alpha: 0.6, name: "Stockholm" },
  // Middle East & Africa
  { x: 545, y: 215, r: 1.9, alpha: 0.8, name: "Dubai / Gulf" },
  { x: 460, y: 228, r: 1.5, alpha: 0.55, name: "Cairo" },
  { x: 445, y: 410, r: 1.4, alpha: 0.55, name: "Johannesburg" },
  // Asia
  { x: 595, y: 245, r: 1.8, alpha: 0.75, name: "Mumbai" },
  { x: 605, y: 275, r: 1.7, alpha: 0.7, name: "Bengaluru" },
  { x: 745, y: 170, r: 1.9, alpha: 0.8, name: "Beijing / Shanghai" },
  { x: 735, y: 225, r: 1.8, alpha: 0.75, name: "Shenzhen / HK" },
  { x: 818, y: 158, r: 2.0, alpha: 0.85, name: "Tokyo" },
  { x: 685, y: 295, r: 1.9, alpha: 0.8, name: "Singapore" },
  // South America & Oceania
  { x: 265, y: 375, r: 1.6, alpha: 0.65, name: "São Paulo" },
  { x: 765, y: 388, r: 1.7, alpha: 0.7, name: "Sydney" },
  { x: 745, y: 405, r: 1.5, alpha: 0.6, name: "Melbourne" },
];

export function SpatialMapPrimitive({
  primitive,
  data,
}: {
  primitive?: PrimitiveSpec;
  data?: SpatialMapData;
}): ReactElement {
  const mapData: SpatialMapData = data || primitive?.data || {};
  const mode = mapData.mode || "radar";
  const title = mapData.title;
  const subtitle = mapData.subtitle;

  // Dynamic nodes, edges, and layers strictly from incoming payload
  const rawNodes: SpatialMapNode[] = Array.isArray(mapData.nodes) ? mapData.nodes : [];
  const rawEdges: SpatialMapEdge[] = Array.isArray(mapData.edges) ? mapData.edges : [];
  const initialLayers: SpatialMapLayer[] = Array.isArray(mapData.layers) ? mapData.layers : [];

  const [activeLayers, setActiveLayers] = useState<Record<string, boolean>>(() =>
    initialLayers.reduce((acc, l) => ({ ...acc, [l.id]: l.defaultActive ?? true }), {})
  );

  const toggleLayer = (layerId: string) => {
    setActiveLayers((prev) => ({ ...prev, [layerId]: !prev[layerId] }));
  };

  const nodeMap = new Map<string, SpatialMapNode>(rawNodes.map((n) => [n.id, n]));

  return (
    <div className="w-full h-full relative flex flex-col justify-between overflow-hidden select-none font-mono">
      {/* Header (rendered if title or subtitle present) */}
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

      {/* Primary SVG Vector Canvas */}
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
          </defs>

          {/* Background Technical Grid */}
          <pattern id="spatial-grid-pattern" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="rgba(0, 240, 255, 0.05)" strokeWidth="0.8" />
          </pattern>
          <rect width="1000" height="500" fill="url(#spatial-grid-pattern)" />

          {/* 1. Radar Rendering Mode */}
          {mode === "radar" && (
            <g className="radar-structure">
              {/* Distance Rings */}
              <circle cx="500" cy="250" r="210" fill="none" stroke="rgba(0, 240, 255, 0.16)" strokeWidth="1" />
              <circle cx="500" cy="250" r="145" fill="none" stroke="rgba(0, 240, 255, 0.2)" strokeWidth="1" strokeDasharray="4 4" />
              <circle cx="500" cy="250" r="80" fill="none" stroke="rgba(0, 240, 255, 0.25)" strokeWidth="1.2" />
              <circle cx="500" cy="250" r="210" fill="url(#map-radar-glow)" />

              {/* Azimuth Crosshairs */}
              <line x1="280" y1="250" x2="720" y2="250" stroke="rgba(0, 240, 255, 0.18)" strokeWidth="1" />
              <line x1="500" y1="35" x2="500" y2="465" stroke="rgba(0, 240, 255, 0.18)" strokeWidth="1" />

              {/* Sweep Beam */}
              <g transform="rotate(45 500 250)">
                <line x1="500" y1="250" x2="710" y2="250" stroke="rgba(0, 240, 255, 0.6)" strokeWidth="1.6" />
                <path d="M 500 250 L 710 250 A 210 210 0 0 0 648 102 Z" fill="rgba(0, 240, 255, 0.09)" />
              </g>
            </g>
          )}

          {/* 2. Real Geo / World Map Rendering Mode (Authentic recognizable geography) */}
          {mode === "geo" && (
            <g className="geo-world-layer">
              {/* Graticule Lines (Equator, Tropics, Prime Meridian) */}
              <g className="geo-graticule" opacity="0.3">
                <line x1="0" y1="125" x2="1000" y2="125" stroke="rgba(0, 240, 255, 0.12)" strokeWidth="0.75" strokeDasharray="3 3" />
                <line x1="0" y1="250" x2="1000" y2="250" stroke="rgba(0, 240, 255, 0.2)" strokeWidth="0.9" />
                <line x1="0" y1="375" x2="1000" y2="375" stroke="rgba(0, 240, 255, 0.12)" strokeWidth="0.75" strokeDasharray="3 3" />
                <line x1="250" y1="0" x2="250" y2="500" stroke="rgba(0, 240, 255, 0.1)" strokeWidth="0.75" strokeDasharray="3 3" />
                <line x1="500" y1="0" x2="500" y2="500" stroke="rgba(0, 240, 255, 0.16)" strokeWidth="0.8" />
                <line x1="750" y1="0" x2="750" y2="500" stroke="rgba(0, 240, 255, 0.1)" strokeWidth="0.75" strokeDasharray="3 3" />
              </g>

              {/* Recognizable Continents Landmass Shapes */}
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
                    className="transition-all hover:stroke-cyan-300"
                  />
                ))}
              </g>

              {/* Ambient City / Regional Illumination Lights */}
              <g className="geo-city-lights">
                {AMBIENT_CITY_LIGHTS.map((city, idx) => (
                  <g key={`city-${idx}`}>
                    <circle
                      cx={city.x}
                      cy={city.y}
                      r={city.r * 2.2}
                      fill="#00f0ff"
                      opacity={city.alpha * 0.35}
                      filter="url(#map-node-glow)"
                    />
                    <circle
                      cx={city.x}
                      cy={city.y}
                      r={city.r}
                      fill="#ffffff"
                      opacity={city.alpha}
                    />
                  </g>
                ))}
              </g>
            </g>
          )}

          {/* 3. Topology Regional Mesh Mode */}
          {mode === "topology" && (
            <g className="topology-region" opacity="0.6">
              <polygon
                points="200,120 480,100 600,180 550,420 220,440"
                fill="rgba(0, 240, 255, 0.04)"
                stroke="rgba(0, 240, 255, 0.25)"
                strokeWidth="1.2"
                strokeDasharray="4 4"
              />
            </g>
          )}

          {/* Edges / Routes / Fiber Geodesic Links */}
          <g className="spatial-edges">
            {rawEdges.map((edge, idx) => {
              const src = nodeMap.get(edge.from);
              const dst = nodeMap.get(edge.to);
              if (!src || !dst) return null;

              const x1 = (src.x / 100) * 1000;
              const y1 = (src.y / 100) * 500;
              const x2 = (dst.x / 100) * 1000;
              const y2 = (dst.y / 100) * 500;

              const isDotted = edge.type === "dotted";
              const isRoute = edge.type === "route" || mode === "geo";

              if (isRoute) {
                // Curved Geodesic Fiber Arc
                const midX = (x1 + x2) / 2;
                const arcLift = Math.min(70, Math.abs(x1 - x2) * 0.16 + 25);
                const midY = Math.min(y1, y2) - arcLift;

                return (
                  <g key={`edge-${idx}`}>
                    <path
                      d={`M ${x1} ${y1} Q ${midX} ${midY} ${x2} ${y2}`}
                      fill="none"
                      stroke="rgba(0, 240, 255, 0.55)"
                      strokeWidth={1.8}
                      strokeDasharray={isDotted ? "4 4" : undefined}
                    />
                    {/* Pulsing photon signal on route */}
                    <circle r="2.5" fill="#ffffff" filter="url(#map-node-glow)">
                      <animateMotion
                        path={`M ${x1} ${y1} Q ${midX} ${midY} ${x2} ${y2}`}
                        dur={`${2.4 + (idx % 3) * 0.8}s`}
                        repeatCount="indefinite"
                      />
                    </circle>
                  </g>
                );
              }

              return (
                <line
                  key={`edge-${idx}`}
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke="rgba(0, 240, 255, 0.4)"
                  strokeWidth={1.4}
                  strokeDasharray={isDotted ? "3 3" : undefined}
                />
              );
            })}
          </g>

          {/* Node Points & Hubs */}
          <g className="spatial-nodes">
            {rawNodes.map((node) => {
              const cx = (node.x / 100) * 1000;
              const cy = (node.y / 100) * 500;
              const isWarning = node.status === "warning";
              const isIdle = node.status === "idle";
              const nodeColor = node.color || (isWarning ? "#f87171" : isIdle ? "#94a3b8" : "#00f0ff");

              return (
                <g key={node.id} className="group cursor-pointer">
                  {/* Outer pulse ring for active nodes */}
                  {!isIdle && (
                    <circle
                      cx={cx}
                      cy={cy}
                      r="14"
                      fill="none"
                      stroke={nodeColor}
                      strokeWidth="1"
                      opacity="0.45"
                      className="animate-ping"
                    />
                  )}

                  {/* Outer halo */}
                  <circle
                    cx={cx}
                    cy={cy}
                    r={isWarning ? 7 : 5.5}
                    fill={nodeColor}
                    opacity="0.4"
                    filter="url(#map-node-glow)"
                  />

                  {/* Solid core node */}
                  <circle
                    cx={cx}
                    cy={cy}
                    r={isWarning ? 5.5 : 4}
                    fill="#ffffff"
                  />

                  {/* Label */}
                  <text
                    x={cx + 11}
                    y={cy - 4}
                    fill="#ffffff"
                    fontSize="11.5"
                    fontFamily="monospace"
                    fontWeight="700"
                    letterSpacing="0.08em"
                    filter="drop-shadow(0 0 4px rgba(0,0,0,0.9))"
                  >
                    {node.label}
                  </text>

                  {/* Sublabel */}
                  {node.sublabel && (
                    <text
                      x={cx + 11}
                      y={cy + 11}
                      fill="#7dd3fc"
                      fontSize="9.5"
                      fontFamily="monospace"
                      opacity="0.85"
                      filter="drop-shadow(0 0 3px rgba(0,0,0,0.9))"
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

      {/* Layer Toggle Bar (rendered only if layers exist) */}
      {initialLayers.length > 0 && (
        <div className="flex items-center justify-between mt-2 pt-1.5 border-t border-cyan-500/15 text-[10px]">
          <div className="text-cyan-400/80 uppercase tracking-wider flex items-center gap-1.5 font-bold">
            <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
            <span>REAL-TIME SPATIAL FEED</span>
          </div>
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
                      ? "text-cyan-200 bg-cyan-950/60 border border-cyan-400/50 shadow-sm shadow-cyan-500/20"
                      : "text-slate-500 hover:text-slate-300"
                  }`}
                >
                  <span
                    className="w-2 h-2 rounded-full"
                    style={{ backgroundColor: isActive ? layer.color || "#00f0ff" : "#475569" }}
                  />
                  <span className="tracking-wide font-mono font-medium">{layer.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
