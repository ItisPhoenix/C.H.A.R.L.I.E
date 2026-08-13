import type { ReactElement } from "react";
import { motion, useReducedMotion } from "motion/react";
import { useCharlieStore } from "../store/charlie";

const SPOKE_ANGLES = Array.from({ length: 24 }, (_, index) => index * 15);
const NODES = [
  [384, 103, 2.5], [548, 147, 3], [653, 270, 2.3], [691, 384, 3.4], [594, 574, 2.4],
  [384, 664, 3.8], [208, 598, 2.2], [104, 384, 3.3], [226, 205, 2.1], [458, 262, 3.5],
  [525, 408, 2.1], [312, 485, 2.4], [269, 353, 2.1], [438, 524, 2.2],
] as const;

const PLASMA_PARTICLES = Array.from({ length: 180 }, (_, index) => {
  const angle = index * 137.5;
  const radius = 122 + ((index * 47) % 220);
  const radians = angle * Math.PI / 180;
  return {
    cx: 384 + Math.cos(radians) * radius,
    cy: 384 + Math.sin(radians) * radius,
    r: 0.8 + (index % 4) * 0.45,
    opacity: 0.2 + (index % 5) * 0.12,
  };
});

const ORB_PARTICLES = Array.from({ length: 220 }, (_, index) => {
  const angle = index * 137.508;
  const radius = 106 + ((index * 29) % 86);
  const radians = angle * Math.PI / 180;
  return {
    cx: 384 + Math.cos(radians) * radius,
    cy: 384 + Math.sin(radians) * radius * 0.72,
    r: 0.7 + (index % 5) * 0.35,
    opacity: 0.25 + (index % 7) * 0.09,
  };
});

export function Ring(): ReactElement {
  const coreState = useCharlieStore((state) => state.coreState);
  const connected = useCharlieStore((state) => state.connected);
  const audioLevel = useCharlieStore((state) => state.audioLevel);
  const reduceMotion = useReducedMotion();
  const label = connected ? coreState : "Offline";
  const energyScale = reduceMotion ? 1 : 1 + audioLevel * 0.055;

  return (
    <motion.div
      className="hud-ring"
      data-state={label.toLowerCase()}
      animate={{ scale: energyScale }}
      transition={{ type: "spring", stiffness: 240, damping: 22, mass: 0.38 }}
    >
      <svg className="hud-ring-svg" viewBox="0 0 768 768" role="img" aria-label={`Charlie ${label}`} data-audio-level={audioLevel} data-state={label.toLowerCase()}>
        <defs>
          <filter id="ringGlow" x="-100%" y="-100%" width="300%" height="300%">
            <feGaussianBlur stdDeviation="2.6" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <filter id="softGlow" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="1.5" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <filter id="nodeGlow" x="-400%" y="-400%" width="900%" height="900%">
            <feGaussianBlur stdDeviation="4" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <radialGradient id="ringBloom">
            <stop offset="0" stopColor="#0a9cff" stopOpacity=".2" />
            <stop offset=".44" stopColor="#006ec5" stopOpacity=".08" />
            <stop offset="1" stopColor="#00345e" stopOpacity="0" />
          </radialGradient>
          <filter id="plasmaDisplace" x="-20%" y="-20%" width="140%" height="140%">
            <feTurbulence type="fractalNoise" baseFrequency=".035" numOctaves="2" seed="7" result="noise" />
            <feDisplacementMap in="SourceGraphic" in2="noise" scale="16" />
          </filter>
          <radialGradient id="plasmaCore">
            <stop offset="0" stopColor="var(--plasma-hot)" stopOpacity=".95" />
            <stop offset=".24" stopColor="var(--plasma-mid)" stopOpacity=".82" />
            <stop offset=".7" stopColor="var(--plasma-mid)" stopOpacity=".2" />
            <stop offset="1" stopColor="var(--plasma-deep)" stopOpacity="0" />
          </radialGradient>
        </defs>

        <g className="ring-plasma" filter="url(#plasmaDisplace)">
          <circle cx="384" cy="384" r="250" fill="url(#plasmaCore)" />
          {PLASMA_PARTICLES.map((particle, index) => <circle key={index} {...particle} />)}
        </g>

        <circle cx="384" cy="384" r="306" fill="url(#ringBloom)" />
        <g className="ring-spokes">
          {SPOKE_ANGLES.map((angle) => <line key={angle} x1="384" y1="384" x2="384" y2="80" transform={`rotate(${angle} 384 384)`} />)}
        </g>
        <g className="ring-rotate-slow">
          <circle className="ring-arc" cx="384" cy="384" r="307" strokeWidth="1" strokeDasharray="88 30 4 24 154 46" />
          <circle className="ring-dashed" cx="384" cy="384" r="286" strokeWidth="1" />
          <circle className="ring-arc" cx="384" cy="384" r="265" strokeWidth="1.4" strokeDasharray="35 19 108 14 210 41" />
          <circle className="ring-dim" cx="384" cy="384" r="244" strokeWidth="1" strokeDasharray="220 68" />
        </g>
        <g className="ring-rotate-reverse">
          <circle className="ring-arc" cx="384" cy="384" r="222" strokeWidth="1.5" strokeDasharray="96 34 7 21 148 62" />
          <circle className="ring-dashed" cx="384" cy="384" r="204" strokeWidth="1" />
          <circle className="ring-hairline" cx="384" cy="384" r="186" strokeWidth="1" strokeDasharray="250 58" />
          <circle className="ring-bright" cx="384" cy="384" r="165" strokeWidth="2" strokeDasharray="140 23 54 17 128 69" />
        </g>
        <circle className="ring-hairline" cx="384" cy="384" r="142" strokeWidth="1.1" />
        <circle className="ring-dashed" cx="384" cy="384" r="125" strokeWidth="1.2" />
        {NODES.map(([cx, cy, radius], index) => <circle className="ring-node" key={index} cx={cx} cy={cy} r={radius} />)}
        <path className="ring-bright" d="M384 111v34M384 623v34M111 384h34M623 384h34" strokeWidth="1.6" />
        <path className="ring-hairline" d="M384 72v48M384 648v48M72 384h48M648 384h48" strokeWidth=".6" strokeDasharray="2 4" />
      </svg>

      <svg className="ring-orb-code" viewBox="0 0 768 768" aria-hidden="true">
        <defs>
          <radialGradient id="orbCodeGlow">
            <stop offset="0" stopColor="var(--plasma-hot)" stopOpacity=".98" />
            <stop offset=".22" stopColor="var(--plasma-mid)" stopOpacity=".82" />
            <stop offset=".58" stopColor="var(--plasma-mid)" stopOpacity=".2" />
            <stop offset="1" stopColor="var(--plasma-deep)" stopOpacity="0" />
          </radialGradient>
          <filter id="orbCodeGlowFilter" x="-100%" y="-100%" width="300%" height="300%">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>
        <circle cx="384" cy="384" r="218" fill="url(#orbCodeGlow)" opacity=".8" />
        <g className="orb-code-particles" filter="url(#orbCodeGlowFilter)">
          {ORB_PARTICLES.map((particle, index) => <circle key={index} {...particle} />)}
        </g>
        <g className="orb-code-arcs" fill="none" stroke="var(--plasma-mid)" strokeLinecap="round">
          <ellipse cx="384" cy="384" rx="164" ry="92" strokeWidth="2" strokeDasharray="2 13 22 8" />
          <ellipse cx="384" cy="384" rx="188" ry="108" strokeWidth="1.5" strokeDasharray="1 18 31 11" transform="rotate(58 384 384)" />
          <ellipse cx="384" cy="384" rx="206" ry="118" strokeWidth="1.4" strokeDasharray="3 21 42 16" transform="rotate(-42 384 384)" />
        </g>
      </svg>

      <div className="ring-core">
        <div className="ring-core-copy">
          <strong className="ring-name">CHARLIE</strong>
          <span className="ring-status">{label}</span>
        </div>
      </div>
    </motion.div>
  );
}
