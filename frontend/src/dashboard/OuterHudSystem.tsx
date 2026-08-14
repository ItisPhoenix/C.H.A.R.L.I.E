import type { ReactElement } from "react";

interface ArcSpec {
  radius: number;
  start: number;
  end: number;
  className: string;
}

interface BlockSpec {
  angle: number;
  radius: number;
  width: number;
  height: number;
  hot?: boolean;
  cool?: boolean;
}

const CENTER = 500;

const MAJOR_ARCS: ArcSpec[] = [
  { radius: 432, start: -80, end: -13, className: "hud-arc-primary" },
  { radius: 432, start: 4, end: 67, className: "hud-arc-primary hud-arc-hot" },
  { radius: 432, start: 82, end: 137, className: "hud-arc-primary" },
  { radius: 432, start: 151, end: 192, className: "hud-arc-primary hud-arc-hot" },
  { radius: 432, start: 211, end: 267, className: "hud-arc-primary" },
  { radius: 432, start: 282, end: 329, className: "hud-arc-primary hud-arc-hot" },
  { radius: 432, start: 340, end: 352, className: "hud-arc-primary" },
];

const SECONDARY_ARCS: ArcSpec[] = [
  { radius: 416, start: -86, end: -54, className: "hud-arc-secondary" },
  { radius: 416, start: -44, end: 2, className: "hud-arc-secondary hud-arc-hot" },
  { radius: 416, start: 12, end: 49, className: "hud-arc-secondary" },
  { radius: 416, start: 59, end: 95, className: "hud-arc-secondary" },
  { radius: 416, start: 111, end: 142, className: "hud-arc-secondary hud-arc-hot" },
  { radius: 416, start: 155, end: 184, className: "hud-arc-secondary" },
  { radius: 416, start: 198, end: 232, className: "hud-arc-secondary" },
  { radius: 416, start: 245, end: 276, className: "hud-arc-secondary hud-arc-hot" },
  { radius: 416, start: 289, end: 323, className: "hud-arc-secondary" },
  { radius: 416, start: 334, end: 356, className: "hud-arc-secondary" },
  { radius: 397, start: -76, end: -22, className: "hud-arc-fine" },
  { radius: 397, start: -5, end: 48, className: "hud-arc-fine" },
  { radius: 397, start: 71, end: 119, className: "hud-arc-fine hud-arc-hot" },
  { radius: 397, start: 138, end: 197, className: "hud-arc-fine" },
  { radius: 397, start: 219, end: 263, className: "hud-arc-fine" },
  { radius: 397, start: 281, end: 333, className: "hud-arc-fine" },
];

const INNER_ARCS: ArcSpec[] = [
  { radius: 374, start: -74, end: -24, className: "hud-arc-inner" },
  { radius: 364, start: -7, end: 48, className: "hud-arc-inner" },
  { radius: 373, start: 67, end: 118, className: "hud-arc-inner" },
  { radius: 361, start: 138, end: 188, className: "hud-arc-inner" },
  { radius: 374, start: 207, end: 258, className: "hud-arc-inner" },
  { radius: 363, start: 280, end: 328, className: "hud-arc-inner" },
];

const BLOCKS: BlockSpec[] = [
  { angle: -82, radius: 420, width: 29, height: 8, hot: true },
  { angle: -74, radius: 405, width: 15, height: 5 },
  { angle: -63, radius: 414, width: 10, height: 5 },
  { angle: -49, radius: 422, width: 22, height: 7, hot: true },
  { angle: -36, radius: 407, width: 12, height: 5 },
  { angle: -18, radius: 418, width: 31, height: 8, hot: true },
  { angle: -5, radius: 405, width: 16, height: 5 },
  { angle: 16, radius: 419, width: 13, height: 5 },
  { angle: 31, radius: 408, width: 26, height: 7, hot: true },
  { angle: 47, radius: 420, width: 10, height: 5 },
  { angle: 63, radius: 411, width: 17, height: 6 },
  { angle: 79, radius: 422, width: 30, height: 8, hot: true },
  { angle: 96, radius: 408, width: 12, height: 5 },
  { angle: 113, radius: 417, width: 24, height: 7, hot: true },
  { angle: 127, radius: 405, width: 11, height: 5 },
  { angle: 143, radius: 421, width: 15, height: 6 },
  { angle: 158, radius: 409, width: 29, height: 8, hot: true },
  { angle: 177, radius: 420, width: 12, height: 5 },
  { angle: 196, radius: 407, width: 18, height: 6 },
  { angle: 211, radius: 421, width: 27, height: 8, hot: true },
  { angle: 229, radius: 409, width: 11, height: 5 },
  { angle: 246, radius: 418, width: 14, height: 6 },
  { angle: 260, radius: 405, width: 31, height: 8, hot: true },
  { angle: 278, radius: 422, width: 12, height: 5 },
  { angle: 293, radius: 408, width: 20, height: 7, hot: true },
  { angle: 309, radius: 419, width: 10, height: 5 },
  { angle: 324, radius: 407, width: 25, height: 8, hot: true },
  { angle: 342, radius: 420, width: 13, height: 5, cool: true },
];

function pointOnCircle(radius: number, degrees: number): { x: number; y: number } {
  const radians = (degrees - 90) * Math.PI / 180;
  return {
    x: CENTER + radius * Math.cos(radians),
    y: CENTER + radius * Math.sin(radians),
  };
}

function arcPath(radius: number, start: number, end: number): string {
  const from = pointOnCircle(radius, start);
  const to = pointOnCircle(radius, end);
  const span = ((end - start) % 360 + 360) % 360;
  return `M ${from.x.toFixed(2)} ${from.y.toFixed(2)} A ${radius} ${radius} 0 ${span > 180 ? 1 : 0} 1 ${to.x.toFixed(2)} ${to.y.toFixed(2)}`;
}

function ArcLayer({ arcs }: { arcs: ArcSpec[] }): ReactElement {
  return (
    <>
      {arcs.map((arc, index) => (
        <path
          key={`${arc.radius}-${arc.start}-${index}`}
          d={arcPath(arc.radius, arc.start, arc.end)}
          className={arc.className}
        />
      ))}
    </>
  );
}

export function OuterHudSystem(): ReactElement {
  return (
    <svg className="hud-outer-svg" viewBox="0 0 1000 1000" aria-hidden="true">
      <defs>
        <filter id="hud-vector-glow" x="-35%" y="-35%" width="170%" height="170%">
          <feGaussianBlur stdDeviation="5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter id="hud-vector-soft-glow" x="-25%" y="-25%" width="150%" height="150%">
          <feGaussianBlur stdDeviation="2.3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <g className="hud-vector-structure">
        <circle cx={CENTER} cy={CENTER} r="438" />
        <circle cx={CENTER} cy={CENTER} r="424" />
        <circle cx={CENTER} cy={CENTER} r="405" />
        <circle cx={CENTER} cy={CENTER} r="386" />
        <circle cx={CENTER} cy={CENTER} r="329" className="hud-vector-structure-cool" />
      </g>

      <g className="hud-vector-core-shell hud-vector-core-drift">
        <circle cx={CENTER} cy={CENTER} r="99" className="hud-vector-core-boundary" />
        <circle cx={CENTER} cy={CENTER} r="89" className="hud-vector-core-inner" />
        {[0, 32, 67, 103, 141].map((angle, index) => (
          <ellipse
            key={angle}
            cx={CENTER}
            cy={CENTER}
            rx="97"
            ry={22 + index * 2.4}
            className={index === 2 ? "hud-vector-core-hot" : undefined}
            transform={`rotate(${angle} ${CENTER} ${CENTER})`}
          />
        ))}
        {[-54, -27, 0, 27, 54].map((offset) => (
          <ellipse
            key={offset}
            cx={CENTER}
            cy={CENTER + offset}
            rx={Math.sqrt(Math.max(0, 99 * 99 - offset * offset))}
            ry="13"
            className="hud-vector-core-latitude"
          />
        ))}
      </g>

      <g className="hud-vector-major">
        <ArcLayer arcs={MAJOR_ARCS} />
      </g>
      <g className="hud-vector-secondary hud-vector-drift-reverse">
        <ArcLayer arcs={SECONDARY_ARCS} />
      </g>
      <g className="hud-vector-inner hud-vector-drift">
        <ArcLayer arcs={INNER_ARCS} />
      </g>

      <g className="hud-vector-blocks hud-vector-drift-reverse">
        {BLOCKS.map((block, index) => (
          <rect
            key={`${block.angle}-${index}`}
            x={CENTER - block.width / 2}
            y={CENTER - block.radius - block.height / 2}
            width={block.width}
            height={block.height}
            rx={block.height * 0.16}
            className={block.cool ? "hud-vector-block-cool" : block.hot ? "hud-vector-block-hot" : undefined}
            transform={`rotate(${block.angle} ${CENTER} ${CENTER})`}
          />
        ))}
      </g>

      <g className="hud-vector-ticks hud-vector-drift">
        {Array.from({ length: 128 }, (_, index) => {
          if (index % 19 === 3 || index % 23 === 8) return null;
          const angle = index / 128 * 360;
          const major = index % 16 === 0;
          const medium = index % 4 === 0;
          const inner = major ? 373 : medium ? 379 : 384;
          const outer = major ? 397 : medium ? 395 : 392;
          return (
            <line
              key={index}
              x1={CENTER}
              y1={CENTER - inner}
              x2={CENTER}
              y2={CENTER - outer}
              className={major ? "hud-vector-tick-major" : medium ? "hud-vector-tick-medium" : undefined}
              transform={`rotate(${angle} ${CENTER} ${CENTER})`}
            />
          );
        })}
      </g>

      <g className="hud-vector-dots hud-vector-drift-reverse">
        {Array.from({ length: 132 }, (_, index) => {
          const angle = index / 132 * 360;
          const emphasized = index % 22 === 0;
          const cool = index === 17 || index === 91;
          return (
            <circle
              key={index}
              cx={CENTER}
              cy={160}
              r={emphasized ? 2.25 : 1.12}
              className={cool ? "hud-vector-dot-cool" : emphasized ? "hud-vector-dot-hot" : undefined}
              transform={`rotate(${angle} ${CENTER} ${CENTER})`}
            />
          );
        })}
      </g>

      <g className="hud-vector-nodes">
        {[-78, -51, -18, 11, 42, 77, 111, 148, 183, 218, 254, 289, 322, 347].map((angle, index) => {
          const point = pointOnCircle(index % 3 === 0 ? 451 : 444, angle);
          return <circle key={angle} cx={point.x} cy={point.y} r={index % 3 === 0 ? 3.5 : 2} className={index === 4 ? "hud-vector-node-cool" : undefined} />;
        })}
      </g>
    </svg>
  );
}
