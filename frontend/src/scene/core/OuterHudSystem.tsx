import type { ReactElement } from "react";

const CENTER = 500;

interface OrbitPlanet {
  cx: number;
  cy: number;
  radius: number;
  size: number;
  haloSize: number;
  haloOpacity: number;
  color?: string;
  hasTrail?: boolean;
}

export function OuterHudSystem(): ReactElement {
  // Exact sparse orbital nodes with cyan halos matching the approved reference
  const PLANETS: OrbitPlanet[] = [
    // 1. West / 9 o'clock planet on main orbital track
    { cx: 105, cy: 500, radius: 395, size: 4.5, haloSize: 13, haloOpacity: 0.55, color: "#ffffff" },
    // 2. North-East / 1 o'clock planet at head of energy trail
    { cx: 725, cy: 220, radius: 425, size: 4.2, haloSize: 12, haloOpacity: 0.6, color: "#ffffff", hasTrail: true },
    // 3. East / 3 o'clock outer planet
    { cx: 945, cy: 460, radius: 450, size: 3.8, haloSize: 10, haloOpacity: 0.45, color: "#ffffff" },
    // 4. Upper small spark node (~11:30)
    { cx: 430, cy: 135, radius: 370, size: 2.5, haloSize: 7, haloOpacity: 0.45, color: "#00f0ff" },
    // 5. South-East wisp node (~5 o'clock)
    { cx: 830, cy: 740, radius: 430, size: 2.8, haloSize: 8, haloOpacity: 0.45, color: "#00f0ff" },
    // 6. South-West node (~7 o'clock)
    { cx: 280, cy: 820, radius: 440, size: 2.4, haloSize: 6, haloOpacity: 0.4, color: "#38bdf8" },
  ];

  return (
    <svg className="hud-outer-svg" viewBox="0 0 1000 1000" aria-hidden="true">
      <defs>
        <filter id="hud-bead-glow" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="3.5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <linearGradient id="hud-trail-1" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.95" />
          <stop offset="35%" stopColor="#00f0ff" stopOpacity="0.75" />
          <stop offset="70%" stopColor="#0077b6" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#00f0ff" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="hud-trail-2" x1="100%" y1="100%" x2="0%" y2="0%">
          <stop offset="0%" stopColor="#00f0ff" stopOpacity="0.8" />
          <stop offset="60%" stopColor="#0077b6" stopOpacity="0.2" />
          <stop offset="100%" stopColor="#00f0ff" stopOpacity="0" />
        </linearGradient>
      </defs>

      {/* 1. Thin Concentric Orbital Guide Rings (Sparse, subtle) */}
      <g className="hud-vector-structure opacity-45">
        {/* Inner Guide Orbit */}
        <circle cx={CENTER} cy={CENTER} r="370" fill="none" stroke="rgba(0, 240, 255, 0.16)" strokeWidth="0.8" />
        {/* Main Orbit */}
        <circle cx={CENTER} cy={CENTER} r="395" fill="none" stroke="rgba(0, 240, 255, 0.28)" strokeWidth="1.0" />
        {/* Secondary Dashed Orbit */}
        <circle cx={CENTER} cy={CENTER} r="430" fill="none" stroke="rgba(0, 240, 255, 0.18)" strokeWidth="0.9" strokeDasharray="5 7" />
        {/* Outer Orbit */}
        <circle cx={CENTER} cy={CENTER} r="455" fill="none" stroke="rgba(0, 240, 255, 0.12)" strokeWidth="0.8" />
      </g>

      {/* 2. Sweeping Curved Energy Wisps / Trails */}
      <g className="hud-vector-secondary hud-vector-drift">
        {/* Upper-Right Sweeping Arc from 1 o'clock */}
        <path
          d="M 725 220 A 425 425 0 0 1 895 620"
          fill="none"
          stroke="url(#hud-trail-1)"
          strokeWidth="1.8"
          strokeLinecap="round"
        />
        {/* Bottom-Right Filament Arc */}
        <path
          d="M 830 740 A 430 430 0 0 1 450 925"
          fill="none"
          stroke="url(#hud-trail-2)"
          strokeWidth="1.4"
          strokeLinecap="round"
        />
        {/* Left Orbit Segment */}
        <path
          d="M 125 360 A 395 395 0 0 0 105 500"
          fill="none"
          stroke="rgba(0, 240, 255, 0.45)"
          strokeWidth="1.4"
          strokeLinecap="round"
        />
      </g>

      {/* 3. Orbiting Planets / Glowing Beads */}
      <g className="hud-vector-nodes hud-vector-drift">
        {PLANETS.map((planet, idx) => (
          <g key={`planet-${idx}`}>
            {/* Outer Cyan Halo */}
            <circle
              cx={planet.cx}
              cy={planet.cy}
              r={planet.haloSize}
              fill={planet.color || "#00f0ff"}
              opacity={planet.haloOpacity}
              filter="url(#hud-bead-glow)"
            />
            {/* Crisp Inner Node */}
            <circle
              cx={planet.cx}
              cy={planet.cy}
              r={planet.size}
              fill={planet.color || "#ffffff"}
              filter="url(#hud-bead-glow)"
            />
          </g>
        ))}
      </g>

      {/* 4. Subtle Perimeter Azimuth Coordinates */}
      <g className="hud-vector-ticks opacity-45">
        {/* 36 Perimeter dots around radius 475 */}
        {Array.from({ length: 36 }, (_, i) => {
          const deg = i * 10;
          const rad = (deg * Math.PI) / 180;
          const r = 475;
          const isMajor = deg % 90 === 0;
          const isSemiMajor = deg % 30 === 0;

          return (
            <circle
              key={`dot-${deg}`}
              cx={CENTER + r * Math.cos(rad)}
              cy={CENTER + r * Math.sin(rad)}
              r={isMajor ? "1.6" : isSemiMajor ? "1.2" : "0.8"}
              fill={isMajor ? "#ffffff" : isSemiMajor ? "#00f0ff" : "rgba(0, 240, 255, 0.35)"}
            />
          );
        })}
      </g>
    </svg>
  );
}
