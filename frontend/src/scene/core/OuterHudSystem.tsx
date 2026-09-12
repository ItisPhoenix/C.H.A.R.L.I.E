import type { ReactElement } from "react";

const CENTER = 500;

interface OrbitNode {
  cx: number;
  cy: number;
  size: number;
  color: string;
  opacity: number;
}

interface OuterHudSystemProps {
  compact?: boolean;
}

export function OuterHudSystem({ compact = false }: OuterHudSystemProps): ReactElement {
  const nodes: OrbitNode[] = compact
    ? [
        { cx: 176, cy: 326, size: 2.6, color: "#38bdf8", opacity: 0.52 },
        { cx: 806, cy: 196, size: 3, color: "#ffffff", opacity: 0.7 },
      ]
    : [
        { cx: 112, cy: 500, size: 3.2, color: "#ffffff", opacity: 0.62 },
        { cx: 738, cy: 214, size: 3, color: "#ffffff", opacity: 0.72 },
        { cx: 884, cy: 604, size: 2.8, color: "#38bdf8", opacity: 0.5 },
        { cx: 242, cy: 806, size: 2.1, color: "#38bdf8", opacity: 0.4 },
      ];
  const tickCount = compact ? 12 : 20;

  return (
    <svg
      className="hud-outer-svg"
      viewBox="0 0 1000 1000"
      data-core-scale={compact ? "docked" : "centered"}
      data-geometry="orbital-calibration"
      aria-hidden="true"
    >
      <defs>
        <filter id="hud-node-glow" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="1.3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <linearGradient id="hud-arc-cyan" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#d8f8ff" stopOpacity=".62" />
          <stop offset="28%" stopColor="#22d3ee" stopOpacity=".38" />
          <stop offset="100%" stopColor="#22d3ee" stopOpacity="0" />
        </linearGradient>
      </defs>

      <g className="hud-vector-structure" fill="none" strokeLinecap="round">
        <circle cx={CENTER} cy={CENTER} r="366" stroke="rgba(80, 203, 237, .13)" strokeWidth=".9" strokeDasharray="2 18" />
        <circle cx={CENTER} cy={CENTER} r="408" stroke="rgba(80, 203, 237, .1)" strokeWidth=".8" strokeDasharray={compact ? "22 30" : "48 20 8 28"} />
        {!compact && (
          <circle cx={CENTER} cy={CENTER} r="458" stroke="rgba(80, 203, 237, .06)" strokeWidth=".7" strokeDasharray="3 34" />
        )}
        <path d="M 500 56 V 82 M 500 918 V 944 M 56 500 H 82 M 918 500 H 944" stroke="rgba(216, 248, 255, .32)" strokeWidth="1" />
      </g>

      <g className="hud-vector-secondary hud-vector-drift" fill="none" strokeLinecap="round">
        <path d="M 738 214 A 408 408 0 0 1 888 590" stroke="url(#hud-arc-cyan)" strokeWidth="1.3" />
        {!compact && (
          <path d="M 178 748 A 432 432 0 0 1 430 914" stroke="rgba(34, 211, 238, .2)" strokeWidth="1" />
        )}
      </g>

      <g className="hud-vector-nodes hud-vector-drift" filter="url(#hud-node-glow)">
        {nodes.map((node, index) => (
          <g key={`${node.cx}-${node.cy}`}>
            <circle cx={node.cx} cy={node.cy} r={node.size * 3} fill={node.color} opacity={node.opacity * 0.1} />
            <circle cx={node.cx} cy={node.cy} r={node.size} fill={node.color} opacity={node.opacity} />
            {index === 0 && <path d={`M ${node.cx - 13} ${node.cy} H ${node.cx - 5}`} stroke={node.color} strokeOpacity=".42" strokeWidth="1" />}
          </g>
        ))}
      </g>

      <g className="hud-vector-ticks">
        {Array.from({ length: tickCount }, (_, index) => {
          const angle = -Math.PI / 2 + (index / tickCount) * Math.PI * 2;
          const major = index % (compact ? 4 : 5) === 0;
          const inner = major ? 438 : 450;
          const outer = compact ? 462 : 472;
          const x1 = CENTER + Math.cos(angle) * inner;
          const y1 = CENTER + Math.sin(angle) * inner;
          const x2 = CENTER + Math.cos(angle) * outer;
          const y2 = CENTER + Math.sin(angle) * outer;
          return (
            <line
              key={`tick-${index}`}
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              stroke={major ? "rgba(216, 248, 255, .42)" : "rgba(80, 203, 237, .24)"}
              strokeWidth={major ? "1" : ".7"}
            />
          );
        })}
      </g>
    </svg>
  );
}
