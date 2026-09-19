const VIEWBOX_SIZE = 640;
const CENTER = VIEWBOX_SIZE / 2;
const TICK_INNER_RADIUS = 286;
const TICK_OUTER_RADIUS = 303;

// [CHOSEN] Sparse calibration divisions imply a field without forming a clock face.
export const RADIAL_DIVISION_COUNT = 24;

const polarPoint = (radius: number, angle: number) => {
  const radians = ((angle - 90) * Math.PI) / 180;
  return {
    x: CENTER + radius * Math.cos(radians),
    y: CENTER + radius * Math.sin(radians),
  };
};

const radialTicks = Array.from(
  { length: RADIAL_DIVISION_COUNT },
  (_, index) => {
    const angle = (360 / RADIAL_DIVISION_COUNT) * index;
    const inner = polarPoint(TICK_INNER_RADIUS, angle);
    const outer = polarPoint(
      index % 4 === 0 ? TICK_OUTER_RADIUS + 9 : TICK_OUTER_RADIUS,
      angle,
    );

    return (
      <line
        key={angle}
        className={`core__tick${index % 4 === 0 ? " core__tick--major" : ""}`}
        data-core-tick="true"
        x1={inner.x}
        y1={inner.y}
        x2={outer.x}
        y2={outer.y}
      />
    );
  },
);

const calibrationDots = Array.from({ length: 16 }, (_, index) => {
  const angle = index * 22.5 + 7.5;
  const point = polarPoint(index % 3 === 0 ? 304 : 298, angle);
  return (
    <circle
      key={`dot-${angle}`}
      className={`core__marker${index % 3 === 0 ? " core__marker--phase" : ""}`}
      cx={point.x}
      cy={point.y}
      r={index % 3 === 0 ? 2 : 1.35}
    />
  );
});

const energyHighlights = [
  { name: "left", start: 238, dash: "14 86" },
  { name: "upper-left", start: 302, dash: "9 91" },
];

export const coreStates = ["idle", "listening", "thinking", "acting", "speaking",
  "waiting-for-approval", "error", "degraded"] as const;
export type CoreState = typeof coreStates[number];

export function CharlieCore({ state = "idle" }: { state?: CoreState }) {
  const accessibleState = state.replaceAll("-", " ");
  return (
    <div className="core-shell" data-state={state}>
      <svg
        className="charlie-core"
        viewBox={`0 0 ${VIEWBOX_SIZE} ${VIEWBOX_SIZE}`}
        role="img"
        aria-label={`CHARLIE core, ${accessibleState}`}
        aria-labelledby="charlie-core-title charlie-core-description"
      >
        <title id="charlie-core-title">CHARLIE core</title>
        <desc id="charlie-core-description">
          A cyan computational instrument with a deterministic energy cycle.
          Current state: {accessibleState}.
        </desc>

        <defs>
          <radialGradient id="core-atmosphere" cx="50%" cy="50%" r="50%">
            <stop offset="0" stopColor="var(--cyan-core)" stopOpacity="0.14" />
            <stop offset="0.5" stopColor="var(--blue-core)" stopOpacity="0.05" />
            <stop offset="1" stopColor="var(--blue-core)" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="core-disc" cx="50%" cy="42%" r="60%">
            <stop offset="0" stopColor="var(--cyan-core)" stopOpacity="0.04" />
            <stop offset="0.72" stopColor="var(--scene-navy)" stopOpacity="0.08" />
            <stop offset="1" stopColor="var(--scene-ink)" stopOpacity="0.92" />
          </radialGradient>
          <filter id="core-glow" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="12" />
          </filter>
          <filter id="core-ring-glow" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="6" />
          </filter>
        </defs>

        <g className="core__atmosphere" aria-hidden="true">
          <circle cx={CENTER} cy={CENTER} r="214" fill="url(#core-atmosphere)" />
        </g>

        <g className="core__calibration" aria-hidden="true">
          {radialTicks}
          {calibrationDots}
        </g>

        <g className="core__orbit-system" aria-hidden="true">
          <circle
            className="core__orbit core__orbit--secondary"
            data-core-arc="secondary"
            cx={CENTER}
            cy={CENTER}
            r="258"
            pathLength="100"
            strokeDasharray="8 92"
            transform={`rotate(205 ${CENTER} ${CENTER})`}
          />
          <circle
            className="core__orbit core__orbit--tertiary"
            data-core-arc="tertiary"
            cx={CENTER}
            cy={CENTER}
            r="270"
            pathLength="100"
            strokeDasharray="5 95"
            transform={`rotate(28 ${CENTER} ${CENTER})`}
          />
          <circle
            className="core__arc core__arc--primary"
            data-core-arc="primary"
            cx={CENTER}
            cy={CENTER}
            r="244"
            pathLength="100"
            strokeDasharray="11 89"
            transform={`rotate(150 ${CENTER} ${CENTER})`}
          />
        </g>

        <g className="core__energy-system" aria-hidden="true">
          <circle className="core__energized-glow" cx={CENTER} cy={CENTER} r="231"
            pathLength="100" strokeDasharray="97 3" />
          <circle className="core__energized-track" cx={CENTER} cy={CENTER} r="231"
            pathLength="100" strokeDasharray="97 3" />
          <circle className="core__energized-ring" data-core-energy="true" cx={CENTER} cy={CENTER}
            r="231" pathLength="100" strokeDasharray="97 3" />
          <circle className="core__energy-edge" cx={CENTER} cy={CENTER} r="226"
            pathLength="100" strokeDasharray="97 3" />
          {energyHighlights.map(segment => (
            <circle
              key={segment.name}
              className={`core__energy-highlight core__energy-highlight--${segment.name}`}
              data-core-energy-segment={segment.name}
              cx={CENTER}
              cy={CENTER}
              r="250"
              pathLength="100"
              strokeDasharray={segment.dash}
              transform={`rotate(${segment.start} ${CENTER} ${CENTER})`}
            />
          ))}
        </g>

        <g className="core__inner-system">
          <circle
            className="core__identity-disc"
            cx={CENTER}
            cy={CENTER}
            r="161"
            fill="url(#core-disc)"
            aria-hidden="true"
          />
          <circle
            className="core__halo"
            cx={CENTER}
            cy={CENTER}
            r="185"
            aria-hidden="true"
          />
          <circle className="core__inner-ring" data-core-inner-ring="true"
            cx={CENTER} cy={CENTER} r="171" pathLength="100" strokeDasharray="97 3" />
          <text className="core__wordmark" x={CENTER} y={CENTER - 3} textAnchor="middle">
            CHARLIE
          </text>
        </g>

        <circle className="core__state-beacon" cx={CENTER} cy="18" r="3" aria-hidden="true" />
      </svg>
    </div>
  );
}
