import { Fragment, type CSSProperties, type ReactElement, type ReactNode } from "react";
import { CharlieRing } from "../scene/core/CharlieRing";
import { EnvironmentLayer } from "../scene/EnvironmentLayer";
import "../scene/scene.css";
import "./reference-lab.css";

export type ReferenceVisualScenario =
  | "ref-idle"
  | "ref-online"
  | "ref-research"
  | "ref-research-selected"
  | "ref-vision"
  | "ref-vision-selected"
  | "ref-briefing"
  | "ref-briefing-alt"
  | "ref-system-tasks"
  | "ref-active-docked"
  | "ref-approval"
  | "ref-settings"
  | "ref-fault"
  | "ref-degraded";

export const REFERENCE_VISUAL_SCENARIOS = [
  "ref-idle",
  "ref-online",
  "ref-research",
  "ref-research-selected",
  "ref-vision",
  "ref-vision-selected",
  "ref-briefing",
  "ref-briefing-alt",
  "ref-system-tasks",
  "ref-active-docked",
  "ref-approval",
  "ref-settings",
  "ref-fault",
  "ref-degraded",
] as const satisfies readonly ReferenceVisualScenario[];

type CorePosition = "center" | "dock";
type ReferenceTone = "cyan" | "amber" | "fault";

interface ReferenceVisualLabProps {
  scenario: ReferenceVisualScenario;
}

interface ReferenceCoreProps {
  position: CorePosition;
  label: string;
  subtext: string;
  tone?: ReferenceTone;
}

interface ReferencePanelProps {
  eyebrow: string;
  title: string;
  className?: string;
  children: ReactNode;
}

function ReferencePanel({ eyebrow, title, className = "", children }: ReferencePanelProps): ReactElement {
  return (
    <section className={`ref-panel ${className}`.trim()}>
      <div className="ref-panel__heading">
        <span className="ref-eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
      </div>
      {children}
    </section>
  );
}

function ReferenceCore({ position, label, subtext, tone = "cyan" }: ReferenceCoreProps): ReactElement {
  const compact = position === "dock";
  return (
    <div
      className={`ref-core ref-core--${position} ref-core--${tone}`}
      data-reference-core="authoritative-charlie-ring"
      data-core-position={position === "center" ? "center" : "dock_bottom_right"}
      data-core-tone={tone}
      role="img"
      aria-label={`C.H.A.R.L.I.E. ${label}`}
    >
      <div className="ref-core__orb">
        <CharlieRing compact={compact} />
        <span className="ref-core__wordmark">C.H.A.R.L.I.E.</span>
      </div>
      <div className="ref-core__status">
        <span className="ref-core__label">{label}</span>
        <span className="ref-core__subtext">{subtext}</span>
        <span className="ref-core__dots" aria-hidden="true"><i /><i /><i /></span>
      </div>
    </div>
  );
}

function WorkspaceHeading({
  eyebrow,
  title,
  subtitle,
  className = "",
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
  className?: string;
}): ReactElement {
  return (
    <header className={`ref-workspace-heading ${className}`.trim()}>
      <span className="ref-eyebrow">{eyebrow}</span>
      <h1>{title}</h1>
      <p>{subtitle}</p>
    </header>
  );
}

function SectionRule({ label }: { label: string }): ReactElement {
  return <div className="ref-section-rule"><span>{label}</span></div>;
}

function MiniChart({ variant = "research" }: { variant?: "research" | "cpu" }): ReactElement {
  const points = variant === "cpu"
    ? "0,52 20,44 38,49 56,38 73,44 92,31 110,39 128,34 146,42 166,28 184,35 204,24"
    : "0,74 18,65 35,70 52,49 70,58 86,34 104,44 121,22 140,37 158,28 177,12 194,29 214,18 232,34 250,27 270,45";
  const area = `${points} ${variant === "cpu" ? "204,78 0,78" : "270,78 0,78"}`;
  return (
    <svg className="ref-mini-chart" viewBox="0 0 270 90" preserveAspectRatio="none" aria-label={variant === "cpu" ? "CPU usage chart" : "Activity over time chart"} role="img">
      <path className="ref-chart-grid" d="M0 18H270M0 42H270M0 66H270M54 0V90M108 0V90M162 0V90M216 0V90" />
      <polygon className="ref-chart-area" points={area} />
      <polyline className="ref-chart-line" points={points} />
      <circle className="ref-chart-point" cx={variant === "cpu" ? "204" : "177"} cy={variant === "cpu" ? "24" : "12"} r="3" />
    </svg>
  );
}

const RESEARCH_HEATMAP = [
  0.08, 0.12, 0.18, 0.25, 0.31, 0.27, 0.21, 0.16, 0.12, 0.1, 0.08, 0.06,
  0.12, 0.22, 0.35, 0.54, 0.72, 0.62, 0.48, 0.38, 0.28, 0.18, 0.1, 0.06,
  0.2, 0.38, 0.58, 0.76, 0.9, 0.84, 0.7, 0.56, 0.78, 0.92, 0.64, 0.28,
  0.16, 0.3, 0.48, 0.68, 0.82, 0.74, 0.6, 0.72, 0.88, 0.96, 0.78, 0.4,
  0.08, 0.16, 0.26, 0.34, 0.44, 0.4, 0.36, 0.44, 0.54, 0.64, 0.46, 0.2,
];

function ActivityHeatmap(): ReactElement {
  return (
    <div className="ref-heatmap" data-testid="reference-activity-density">
      <div className="ref-heatmap__grid">
        {RESEARCH_HEATMAP.map((value, index) => (
          <span
            key={`heat-${index}`}
            style={{
              opacity: 0.28 + value * 0.72,
              backgroundColor: value > 0.82 ? "#e46f5c" : value > 0.55 ? "#49b9d3" : "#176286",
            }}
          />
        ))}
      </div>
      <div className="ref-heatmap__legend"><span>LOW</span><i /><span>HIGH</span></div>
    </div>
  );
}

function ResearchMap({ selected = false }: { selected?: boolean }): ReactElement {
  const nodes = [
    { x: 495, y: 230, tone: "anomaly" }, { x: 460, y: 195, tone: "signal" },
    { x: 550, y: 268, tone: "anomaly" }, { x: 592, y: 318, tone: "signal" },
    { x: 650, y: 350, tone: "signal" }, { x: 392, y: 290, tone: "signal" },
    { x: 720, y: 260, tone: "signal" }, { x: 310, y: 350, tone: "route" },
  ];
  return (
    <svg className="ref-map-svg" viewBox="0 0 860 520" preserveAspectRatio="none" role="img" aria-label="Northern Seas maritime investigation map">
      <defs>
        <pattern id="ref-research-grid" width="28" height="28" patternUnits="userSpaceOnUse"><path d="M28 0H0V28" fill="none" stroke="#4db7d2" strokeOpacity=".12" /></pattern>
        <linearGradient id="ref-research-sea" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#0c3145" /><stop offset=".55" stopColor="#061825" /><stop offset="1" stopColor="#020a12" /></linearGradient>
        <filter id="ref-map-glow"><feGaussianBlur stdDeviation="8" /></filter>
      </defs>
      <rect width="860" height="520" fill="url(#ref-research-sea)" />
      <rect width="860" height="520" fill="url(#ref-research-grid)" opacity=".55" />
      <path className="ref-map-coast" d="M508 28 548 46 565 81 600 91 609 128 665 137 701 169 746 184 769 220 819 236 845 274 832 311 789 320 760 349 710 354 687 389 637 386 602 411 557 399 524 426 475 410 444 432 400 410 373 381 339 376 321 338 286 327 269 295 286 263 268 226 291 195 313 169 353 163 373 133 414 123 434 94 476 86Z" />
      <path className="ref-map-coast" d="M124 224 145 186 169 177 181 145 207 140 223 162 215 195 235 216 217 249 229 280 205 313 174 301 161 270 132 263Z" />
      <path className="ref-map-coast ref-map-coast--island" d="M402 131 420 105 437 111 449 140 439 178 420 190 405 170 413 151Z" />
      <path className="ref-map-detail" d="M514 52 542 72 548 103 577 116 574 147 602 166 594 196 631 211 646 244 681 250 696 283 731 286 751 312M468 87 488 112 478 142 493 168 474 196 486 224 466 250 480 280 464 314 482 346M353 163 373 188 367 218 389 242 374 269 389 298 378 324M293 268 322 284 332 312 360 331" />
      <path className="ref-map-route" d="M302 343C375 302 417 268 494 230S634 225 782 330" />
      <path className="ref-map-route ref-map-route--dashed" d="M335 394C424 329 469 294 550 268S656 251 742 166" />
      <path className="ref-map-route ref-map-route--faint" d="M190 278C300 239 364 207 435 174S596 151 734 201" />
      <circle className="ref-map-focus" cx="495" cy="230" r="62" />
      <circle className="ref-map-focus ref-map-focus--inner" cx="495" cy="230" r="31" />
      {nodes.map((node, index) => (
        <g key={`research-node-${index}`} className={`ref-map-node ref-map-node--${node.tone}`}>
          <circle cx={node.x} cy={node.y} r={node.tone === "anomaly" ? 11 : 5} />
          <circle cx={node.x} cy={node.y} r={node.tone === "anomaly" ? 4 : 2} />
        </g>
      ))}
      {selected && <path className="ref-selected-callout" d="M495 230 410 126 312 126" />}
      <text className="ref-map-label" x="515" y="216">NORTH SEA</text>
      <text className="ref-map-label ref-map-label--muted" x="512" y="246">ANOMALY CLUSTER</text>
      <text className="ref-map-label ref-map-label--muted" x="90" y="458">SOURCE-BOUND ACTIVITY FIELD</text>
      <text className="ref-map-label ref-map-label--muted" x="676" y="458">LIVE / 72H WINDOW</text>
    </svg>
  );
}

function FindingsStack(): ReactElement {
  const findings = [
    ["INCREASED MARITIME TRAFFIC", "78% increase in vessel activity compared to baseline.", "▰"],
    ["ANOMALOUS CLUSTER DETECTED", "High-density grouping observed outside established routes.", "✣"],
    ["UNIDENTIFIED SIGNALS", "Multiple non-classified transmissions intercepted in region.", "◉"],
    ["PATTERN OF INTEREST", "Repeated behavior aligns with historical smuggling indicators.", "△"],
  ];
  return (
    <div className="ref-findings-list">
      {findings.map(([title, detail, icon]) => (
        <div className="ref-finding" key={title}>
          <span className="ref-finding__icon" aria-hidden="true">{icon}</span>
          <div><strong>{title}</strong><p>{detail}</p></div>
        </div>
      ))}
    </div>
  );
}

function SourceThumb({ kind }: { kind: "satellite" | "ship" | "signal" | "report" | "market" | "oil" | "news" }): ReactElement {
  return (
    <div className={`ref-source-thumb ref-source-thumb--${kind}`} aria-hidden="true">
      <svg viewBox="0 0 180 72" preserveAspectRatio="none">
        <defs><linearGradient id={`thumb-${kind}`} x1="0" y1="0" x2="1" y2="1"><stop stopColor="#21465a" /><stop offset="1" stopColor="#030b13" /></linearGradient></defs>
        <rect width="180" height="72" fill={`url(#thumb-${kind})`} />
        {kind === "ship" || kind === "market" || kind === "oil" || kind === "news" ? <>
          <path d="M22 51 134 49 155 57 43 61Z" fill="#06111b" stroke="#91d1e1" strokeOpacity=".55" />
          <path d="M50 47 61 32 116 33 132 48Z" fill="#18394a" stroke="#79c1d2" strokeOpacity=".5" />
          <path d="M69 32V24M82 32V19M96 32V22" stroke="#bcebf2" strokeOpacity=".55" />
          <path d="M0 64C38 56 68 70 112 62S160 60 180 65" fill="none" stroke="#55bfd9" strokeOpacity=".42" />
        </> : kind === "signal" ? <>
          <path d="M14 37h152M22 30h136M28 44h122" stroke="#4fc4dc" strokeOpacity=".35" />
          {Array.from({ length: 22 }, (_, index) => <path key={`wave-${index}`} d={`M${18 + index * 7} 37v${(index % 5) * 5 - 9}`} stroke="#73e8f5" strokeOpacity=".8" />)}
        </> : <>
          <path d="M16 54 38 28 56 44 82 15 108 42 146 22 166 53" fill="none" stroke="#62d4e9" strokeOpacity=".5" />
          <circle cx="82" cy="15" r="4" fill="#f36f57" />
        </>}
      </svg>
    </div>
  );
}

function SourceRow({ briefing = false, alt = false }: { briefing?: boolean; alt?: boolean }): ReactElement {
  const sources = briefing
    ? alt
      ? [["FINANCIAL TIMES", "Red Sea tensions force major carriers to reroute", "24m ago", "ship"], ["BLOOMBERG", "Global freight rates climb as capacity tightens", "47m ago", "market"], ["REUTERS", "Asian ports brace for delays amid security alerts", "1h ago", "oil"], ["AL JAZEERA", "New satellite imagery shows increased naval activity", "2h ago", "satellite"]]
      : [["MARITIME EXECUTIVE", "Suez Canal delays impact global shipping routes", "16m ago", "ship"], ["BLOOMBERG", "Markets slide on global uncertainty", "32m ago", "market"], ["REUTERS", "Oil surges as supply risks increase", "1h ago", "oil"], ["AL JAZEERA", "G7 leaders to address rising global threats", "2h ago", "news"]]
    : [["SATELLITE FEED", "IR_SAT_2025_05_21", "2 MIN AGO", "satellite"], ["AIS DATA", "NSE_TRAFFIC_LOG_72H", "5 MIN AGO", "ship"], ["SIGNIT INTERCEPT", "SIG_8821_A", "11 MIN AGO", "signal"], ["OPEN SOURCE REPORT", "OSR_2025_05_20", "32 MIN AGO", "report"]];
  return (
    <div className={`ref-source-row ${briefing ? "ref-source-row--briefing" : ""}`}>
      {sources.map(([source, title, age, kind]) => (
        <article className="ref-source-card" key={source}>
          <SourceThumb kind={kind as "satellite" | "ship" | "signal" | "report" | "market" | "oil" | "news"} />
          <div className="ref-source-card__body"><span>{source}</span><strong>{title}</strong><small>{age}</small>{briefing && <em>Read More&nbsp; →</em>}</div>
        </article>
      ))}
    </div>
  );
}

function Timeline({ briefing = false }: { briefing?: boolean }): ReactElement {
  const items = briefing
    ? [["09:15 UTC", "Markets open lower in Asia"], ["11:47 UTC", "Shipping delays reported in the Suez Canal"], ["14:02 UTC", "Energy prices spike 6%"], ["16:30 UTC", "G7 statement expected"]]
    : [["-72h", "BASELINE ESTABLISHED"], ["-48h", "TRAFFIC INCREASE DETECTED"], ["-24h", "ANOMALOUS CLUSTER FORMED"], ["-6h", "UNIDENTIFIED SIGNALS INTERCEPTED"], ["NOW", "ACTIVE MONITORING"]];
  return (
    <div className={`ref-timeline ${briefing ? "ref-timeline--briefing" : ""}`}>
      <div className="ref-timeline__line" />
      {items.map(([time, label], index) => (
        <div className="ref-timeline__item" key={`${time}-${label}`} style={{ "--timeline-index": index } as CSSProperties}>
          <i />
          <strong>{time}</strong>
          <span>{label}</span>
        </div>
      ))}
    </div>
  );
}

function ResearchScreen({ selected = false, screenId }: { selected?: boolean; screenId: string }): ReactElement {
  return (
    <div className={`ref-screen ref-research ${selected ? "ref-research--selected" : ""}`} data-reference-screen={screenId}>
      <WorkspaceHeading eyebrow="RESEARCH WORKSPACE" title="NORTHERN SEAS SURVEILLANCE" subtitle="INCIDENT ANALYSIS & PATTERN RECOGNITION" />
      <div className="ref-research-objective"><span>RESEARCH OBJECTIVE</span><p>Investigate anomalous maritime activity in the Northern Seas region over the past 72 hours and identify behavioral patterns, vessel clusters, and potential risk indicators.</p></div>
      <section className="ref-region ref-research-map"><ResearchMap selected={selected} /><div className="ref-map-caption">◈ REAL-TIME ACTIVITY FEED <small>VESSEL&nbsp;&nbsp; ANOMALY&nbsp;&nbsp; SIGNAL&nbsp;&nbsp; ROUTE</small></div></section>
      <ReferencePanel eyebrow="ACTIVITY OVER TIME" title="" className="ref-research-chart"><MiniChart /><div className="ref-chart-scale"><span>-72h</span><span>-48h</span><span>-24h</span><span>NOW</span></div><strong>↑ 78%</strong></ReferencePanel>
      <ReferencePanel eyebrow="ACTIVITY DENSITY" title="" className="ref-research-heat"><span className="ref-panel__subline">PAST 72 HOURS</span><ActivityHeatmap /></ReferencePanel>
      <ReferencePanel eyebrow="KEY FINDINGS" title="" className="ref-research-findings"><FindingsStack /></ReferencePanel>
      <section className="ref-region ref-research-sources"><SectionRule label="EVIDENCE & SOURCES" /><SourceRow /></section>
      <section className="ref-region ref-research-timeline"><SectionRule label="TIMELINE" /><Timeline /></section>
      {selected && <aside className="ref-anomaly-popup" aria-label="Selected anomaly context"><span className="ref-eyebrow">SELECTED ANOMALY</span><strong><i /> ANOM_2025_05_21_A7</strong><p>HIGH-DENSITY VESSEL CLUSTER</p><small>Unusual gathering of 7 vessels outside established shipping lanes. Behavior not consistent with commercial traffic patterns.</small><dl><dt>TIME</dt><dd>2025-05-21 14:27 UTC</dd><dt>LOCATION</dt><dd>61.238N, 2.115E</dd><dt>VESSELS</dt><dd>7 DETECTED</dd><dt>CONFIDENCE</dt><dd>92%</dd></dl><button type="button">VIEW DETAILED ANALYSIS&nbsp; →</button></aside>}
      <ReferenceCore position="dock" label="IDLE" subtext="I'M HERE WHEN YOU NEED ME." />
    </div>
  );
}

function PortScene({ selected = false }: { selected?: boolean }): ReactElement {
  return (
    <svg className="ref-port-svg" viewBox="0 0 1100 620" preserveAspectRatio="none" role="img" aria-label="TEST/MOCK maritime port surveillance media">
      <defs>
        <linearGradient id="ref-port-sky" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#2a4d62" /><stop offset=".52" stopColor="#122b3c" /><stop offset="1" stopColor="#081824" /></linearGradient>
        <linearGradient id="ref-port-water" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#173b52" /><stop offset="1" stopColor="#06121c" /></linearGradient>
        <linearGradient id="ref-port-light" x1="0" y1="0" x2="1" y2="0"><stop stopColor="#d8f5ff" stopOpacity=".9" /><stop offset="1" stopColor="#5abed6" stopOpacity="0" /></linearGradient>
      </defs>
      <rect width="1100" height="620" fill="url(#ref-port-sky)" />
      <path d="M0 240H1100V620H0Z" fill="url(#ref-port-water)" />
      <g opacity=".72" fill="#c6e4ec">
        {Array.from({ length: 38 }, (_, index) => <circle key={`port-light-${index}`} cx={30 + (index * 71) % 1040} cy={135 + (index * 37) % 92} r={index % 4 === 0 ? 3 : 1.5} />)}
      </g>
      <g fill="none" stroke="#bedce4" strokeOpacity=".24">
        <path d="M0 294H1100M0 368H1100M0 442H1100M0 516H1100M0 580H1100" />
        <path d="M120 620 530 242M300 620 530 242M530 620 530 242M760 620 530 242M970 620 530 242" />
      </g>
      <g stroke="#a8dce8" strokeOpacity=".65" fill="none">
        <path d="M780 255V80M864 255V55M946 255V92M1026 255V68" strokeWidth="4" />
        <path d="M744 255H834L780 80M828 255H914L864 55M909 255H992L946 92M989 255H1076L1026 68" strokeWidth="3" />
      </g>
      <g fill="#172d3b" stroke="#76c8da" strokeOpacity=".45">
        <path d="M40 482 212 448 340 468 325 620H0Z" /><path d="M44 452 104 430 168 447 120 476Z" /><path d="M170 428 244 416 296 437 222 459Z" />
        {Array.from({ length: 16 }, (_, index) => <rect key={`container-${index}`} x={18 + (index % 8) * 38} y={484 + Math.floor(index / 8) * 33} width="32" height="22" fill={index % 3 === 0 ? "#35556a" : "#1c3b4c"} />)}
      </g>
      <path d="M368 383 438 347 650 355 733 407 688 450 445 438Z" fill="#07131e" stroke="#d5edf4" strokeOpacity=".62" strokeWidth="2" />
      <path d="M442 347 475 294 618 300 650 355Z" fill="#25495a" stroke="#a6dce6" strokeOpacity=".48" />
      <path d="M485 294V267M530 294V253M574 296V261" stroke="#c8f0f5" strokeOpacity=".72" strokeWidth="3" />
      <path d="M418 388 685 405M452 407 686 423M486 425 671 438" stroke="url(#ref-port-light)" strokeOpacity=".5" />
      <path d="M520 366 520 432M580 364 580 438M630 371 630 442" stroke="#79d6e4" strokeOpacity=".42" />
      <path d="M790 479 910 463 1050 478 1100 505V620H736Z" fill="#07141f" stroke="#79bfd0" strokeOpacity=".38" />
      <g fill="#e1f7fb"><circle cx="802" cy="451" r="3" /><circle cx="871" cy="426" r="2" /><circle cx="944" cy="448" r="3" /><circle cx="1032" cy="431" r="2" /></g>
      <g stroke="#c0edf4" strokeOpacity=".16"><path d="M718 520H1080M686 558H1070M664 594H1050" /></g>
      <rect width="1100" height="620" fill="url(#ref-port-sky)" opacity=".06" />
      {selected && <path d="M370 350 625 337 744 402 701 456 421 438Z" fill="none" stroke="#f6fbff" strokeOpacity=".35" strokeDasharray="9 8" />}
    </svg>
  );
}

function SatelliteScene(): ReactElement {
  return (
    <svg className="ref-satellite-svg" viewBox="0 0 1100 620" preserveAspectRatio="none" role="img" aria-label="TEST/MOCK satellite frame of selected tanker">
      <defs><linearGradient id="ref-ocean" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#3c5868" /><stop offset=".5" stopColor="#152d3d" /><stop offset="1" stopColor="#07131d" /></linearGradient><pattern id="ref-ocean-grid" width="34" height="34" patternUnits="userSpaceOnUse"><path d="M34 0H0V34" fill="none" stroke="#b5dfea" strokeOpacity=".08" /></pattern></defs>
      <rect width="1100" height="620" fill="url(#ref-ocean)" />
      <path d="M0 83C180 28 330 122 486 64S813 22 1100 108M0 192C173 140 290 258 471 192S822 129 1100 222M0 322C190 263 292 388 490 316S848 252 1100 340M0 478C181 415 374 547 572 449S895 419 1100 493" fill="none" stroke="#bfdbe3" strokeOpacity=".22" strokeWidth="5" />
      <path d="M0 36C194 110 289 12 479 100S815 122 1100 40M0 270C156 350 347 229 524 316S837 377 1100 286M0 550C206 479 347 607 568 528S864 526 1100 581" fill="none" stroke="#7ba7b9" strokeOpacity=".26" strokeWidth="2" />
      <rect width="1100" height="620" fill="url(#ref-ocean-grid)" />
      <g transform="translate(340 136) rotate(15)">
        <path d="M0 116 88 66 382 78 465 142 398 198 88 184Z" fill="#101c24" stroke="#d6e8ed" strokeOpacity=".75" strokeWidth="3" />
        <path d="M103 66 138 16 319 26 382 78Z" fill="#455d68" stroke="#d6e8ed" strokeOpacity=".46" strokeWidth="2" />
        <path d="M144 16V-22M198 21V-39M250 23V-28M305 25V-15" stroke="#d6e8ed" strokeOpacity=".7" strokeWidth="3" />
        <path d="M82 100 382 112M64 128 408 143M54 156 390 173" stroke="#83bac8" strokeOpacity=".5" />
        <path d="M170 65V182M229 69V186M291 72V190" stroke="#98cbd5" strokeOpacity=".4" />
      </g>
      <path d="M532 258H824V472H532Z" fill="none" stroke="#35c8ed" strokeWidth="3" />
      <path d="M532 283V258H557M799 258H824V283M532 447V472H557M799 472H824V447" fill="none" stroke="#73e7f8" strokeWidth="6" />
      <circle cx="678" cy="366" r="19" fill="none" stroke="#71e5f5" strokeWidth="3" /><path d="M638 366H718M678 326V406" stroke="#71e5f5" strokeOpacity=".7" />
      <rect x="844" y="120" width="214" height="74" fill="#06121c" fillOpacity=".72" stroke="#76d8ea" strokeOpacity=".5" /><text className="ref-svg-label" x="864" y="148">TARGET_001</text><text className="ref-svg-label ref-svg-label--small" x="864" y="172">VESSEL CLASS: TANKER · 97%</text><path d="M844 194 824 260" stroke="#55d5ef" strokeWidth="2" />
      <text className="ref-svg-label" x="34" y="42">SATELLITE FEED</text><text className="ref-svg-label ref-svg-label--small" x="34" y="68">IR_SAT_2025_05_21 · 14:02:37Z</text><text className="ref-svg-label ref-svg-label--small" x="966" y="44">CAM-03</text>
    </svg>
  );
}

function VisionMediaFrame({ selected = false }: { selected?: boolean }): ReactElement {
  return (
    <div className="ref-vision-media-frame">
      {selected ? <SatelliteScene /> : <PortScene />}
      {!selected && <>
        <button type="button" className="ref-vessel-box ref-vessel-box--small" aria-label="Select unidentified vessel"><span>VESSEL<br />ID: UNK-317</span></button>
        <button type="button" className="ref-vessel-box ref-vessel-box--main" aria-label="Select MSC Orion"><span>VESSEL<br />MSC ORION · IMO 9234567</span></button>
        <button type="button" className="ref-vessel-box ref-vessel-box--patrol" aria-label="Select patrol craft"><span>SMALL CRAFT<br />SDC-041</span></button>
      </>}
    </div>
  );
}

function TargetOverview({ selected = false }: { selected?: boolean }): ReactElement {
  return (
    <ReferencePanel eyebrow={selected ? "TARGET IDENTIFICATION" : "SELECTED TARGET"} title={selected ? "AL-MAJD" : "MSC ORION"} className="ref-target-overview">
      <div className="ref-target-thumb"><SourceThumb kind="ship" /></div>
      <dl className="ref-data-list">
        {(selected ? [["NAME", "AL-MAJD"], ["TYPE", "OIL TANKER (CRUDE)"], ["FLAG", "UNKNOWN"], ["IMO", "9184327"], ["LENGTH", "274 m"], ["BEAM", "48 m"]] : [["IMO", "9234567"], ["TYPE", "Container Ship"], ["FLAG", "Liberia"], ["STATUS", "Underway"], ["SPEED", "14.2 kn"], ["COURSE", "071°"], ["DESTINATION", "Rotterdam (NLD)"], ["ETA", "2025-05-22 03:40 UTC"]]).map(([key, value]) => <Fragment key={key}><dt>{key}</dt><dd>{value}</dd></Fragment>)}
      </dl>
      {selected && <><div className="ref-target-position"><span>LAST KNOWN POSITION</span><strong>62.4131° N&nbsp;&nbsp; 4.2173° E</strong><small>14:02:37 UTC</small><MiniRouteMap /></div><div className="ref-route-analysis"><span>ROUTE ANALYSIS</span><p>Deviation from reported route<br /><strong>-12.4 NM</strong></p><b>△ POTENTIAL ANOMALY</b></div></>}
      {!selected && <div className="ref-related-media"><SectionRule label="RELATED MEDIA" /><div><SourceThumb kind="ship" /><span>CAM_02 · APPROACH<br /><small>2 MIN AGO</small></span></div><div><SourceThumb kind="satellite" /><span>THERMAL · LONG RANGE<br /><small>6 MIN AGO</small></span></div><strong>+ VIEW MORE MEDIA</strong></div>}
    </ReferencePanel>
  );
}

function MiniRouteMap(): ReactElement {
  return <svg className="ref-route-map" viewBox="0 0 280 110" role="img" aria-label="Last known position route map"><path d="M14 22C64 44 75 81 132 70S192 38 264 83" fill="none" stroke="#a3d9e5" strokeOpacity=".75" strokeDasharray="5 7" /><path d="M14 22 82 52 132 70 198 38 264 83" fill="none" stroke="#4ac9e7" strokeOpacity=".48" /><circle cx="198" cy="38" r="8" fill="none" stroke="#f36959" /><circle cx="198" cy="38" r="3" fill="#f36959" /><path d="M222 0V110M0 55H280" stroke="#63c9de" strokeOpacity=".12" /></svg>;
}

function CameraStrip(): ReactElement {
  return <section className="ref-camera-strip"><SectionRule label="CAMERA VIEWS" /><div>{["CAM_04 / NORTH TERMINAL", "CAM_01 / MAIN CHANNEL", "CAM_02 / EAST DOCKS", "CAM_03 / CONTAINER YARD", "THERMAL_01 / WIDE AREA", "SATELLITE / REGIONAL VIEW"].map((label, index) => <article className={index === 0 ? "is-selected" : ""} key={label}><SourceThumb kind={index === 5 ? "satellite" : index === 4 ? "report" : "ship"} /><span>{label}</span></article>)}</div></section>;
}

function MediaTimeline(): ReactElement {
  return <section className="ref-media-timeline"><SectionRule label="MEDIA TIMELINE" /><div className="ref-media-timeline__row"><button type="button" aria-label="Previous frame">‹</button>{["13:58:12", "13:59:44", "14:01:07", "14:02:37", "14:03:12", "14:05:26", "14:07:03"].map((time, index) => <article className={index === 3 ? "is-selected" : ""} key={time}><SourceThumb kind="ship" /><span>{time}</span></article>)}<button type="button" aria-label="Next frame">›</button></div></section>;
}

function EvidenceAttachments(): ReactElement {
  return <section className="ref-attachments"><SectionRule label="EVIDENCE & ATTACHMENTS" /><div><article><SourceThumb kind="satellite" /><span>SATELLITE FRAME<br /><small>IR_SAT_2025_05_21 · 2 MIN AGO</small></span></article><article><SourceThumb kind="signal" /><span>RADIO INTERCEPT<br /><small>VHF_162.450 · 12 MIN AGO</small></span></article><article><SourceThumb kind="report" /><span>VESSEL REGISTRY<br /><small>REG_AL-MAJD.PDF · 28 MIN AGO</small></span></article><article><SourceThumb kind="ship" /><span>RELATED ACTIVITY<br /><small>GEO-NEARBY-PORTS · 1 HOUR AGO</small></span></article></div></section>;
}

function VisionScreen({ selected = false, screenId }: { selected?: boolean; screenId: string }): ReactElement {
  return (
    <div className={`ref-screen ref-vision ${selected ? "ref-vision--selected" : ""}`} data-reference-screen={screenId}>
      <WorkspaceHeading eyebrow="VISION / MEDIA WORKSPACE" title={selected ? "VISION / MEDIA WORKSPACE" : "VISION / MEDIA WORKSPACE"} subtitle={selected ? "SELECTED TARGET ANALYSIS" : "MARITIME SURVEILLANCE"} />
      <section className="ref-region ref-vision-media"><SectionRule label={selected ? "SATELLITE FEED" : "LIVE FEED"} /><VisionMediaFrame selected={selected} /></section>
      {!selected && <><TargetOverview /><CameraStrip /></>}
      {selected && <><TargetOverview selected /><MediaTimeline /><EvidenceAttachments /></>}
      <ReferenceCore position="dock" label="IDLE" subtext="I'M HERE WHEN YOU NEED ME." />
    </div>
  );
}

function GlobalMap({ alt = false }: { alt?: boolean }): ReactElement {
  const nodes = alt
    ? [[122, 220, "LOS ANGELES"], [210, 335, "PANAMA"], [430, 177, "ROTTERDAM"], [754, 236, "SHANGHAI"], [690, 365, "SINGAPORE"]]
    : [[242, 215, "NEW YORK"], [506, 148, "LONDON"], [647, 250, "DUBAI"], [764, 352, "SINGAPORE"]];
  const landPaths = [
    "M38 166C49 135 75 118 101 112L130 91 170 96 198 82 233 91 263 115 249 143 222 160 226 187 201 204 174 192 153 211 126 198 101 215 73 202 49 207Z",
    "M268 250C291 259 310 280 319 306L312 345 299 371 288 410 268 443 252 411 256 376 242 345 247 304Z",
    "M419 139C444 119 469 120 493 133L520 125 547 139 582 132 612 143 646 136 678 149 711 143 748 162 782 190 772 220 746 237 714 233 697 258 669 257 648 287 620 274 593 300 562 284 535 313 506 292 480 253 449 249 457 216 430 197 442 172 414 163Z",
    "M442 255 470 264 493 291 487 326 470 350 457 385 437 365 428 332 412 306 423 279Z",
    "M594 355C618 344 647 347 669 363L681 387 665 411 624 407 600 391Z",
    "M757 387 798 379 832 396 854 419 835 444 796 442 764 429 744 409Z",
  ];
  return (
    <svg className="ref-global-map-svg" viewBox="0 0 900 520" preserveAspectRatio="none" role="img" aria-label="Global maritime briefing map">
      <defs><pattern id="ref-global-grid" width="28" height="28" patternUnits="userSpaceOnUse"><path d="M28 0H0V28" fill="none" stroke="#4db7d2" strokeOpacity=".1" /></pattern></defs>
      <rect width="900" height="520" fill="url(#ref-global-grid)" />
      {landPaths.map((path, index) => <path className="ref-global-land" d={path} key={`global-land-${index}`} />)}
      <path className="ref-global-route" d={alt ? "M122 220C260 98 320 302 430 177S635 161 754 236 741 331 690 365" : "M242 215C346 173 408 130 506 148S598 206 647 250 727 310 764 352"} />
      <path className="ref-global-route ref-global-route--faint" d={alt ? "M210 335C280 243 356 251 430 177S618 258 690 365" : "M138 182C306 70 435 238 506 148S721 160 822 314"} />
      {nodes.map(([x, y, label]) => <g key={label as string} className={alt ? "ref-global-node ref-global-node--hot" : "ref-global-node"}><circle cx={x as number} cy={y as number} r="17" /><circle cx={x as number} cy={y as number} r="5" /><text x={(x as number) + 25} y={(y as number) - 5}>{label as string}</text><text className="ref-global-node__sub" x={(x as number) + 25} y={(y as number) + 13}>{alt ? "MARITIME NODE" : "VERIFIED SOURCE"}</text></g>)}
    </svg>
  );
}

function BriefingRail({ alt = false }: { alt?: boolean }): ReactElement {
  const headline = alt ? "GLOBAL SHIPPING ROUTES FACE RENEWED DISRUPTIONS AMID REGIONAL INSTABILITY" : "GLOBAL MARKETS REACT TO GEOPOLITICAL TENSIONS AND SUPPLY CHAIN UNCERTAINTY";
  const summary = alt ? "Heightened security incidents in key maritime chokepoints are causing delays and rerouting across major trade lanes. Logistics analysts expect continued volatility in shipping costs and extended delivery timelines through next quarter." : "Escalating tensions in multiple regions are impacting commodity prices and key supply routes. Analysts warn of increased volatility in energy and shipping sectors. Diplomatic efforts underway.";
  return <aside className="ref-briefing-rail"><SectionRule label="BRIEFING / NEWS" /><span className="ref-eyebrow">TOP HEADLINE</span><h2>{headline}</h2><span className="ref-eyebrow">SUMMARY</span><p>{summary}</p><div className="ref-briefing-dots">●　○　○　○</div><SectionRule label="KEY TIMELINE" /><Timeline briefing /></aside>;
}

function BriefingScreen({ alt = false, screenId }: { alt?: boolean; screenId: string }): ReactElement {
  return <div className={`ref-screen ref-briefing ${alt ? "ref-briefing--alt" : ""}`} data-reference-screen={screenId}><div className="ref-briefing-heading"><span className="ref-eyebrow">BRIEFING / NEWS</span></div><section className="ref-region ref-briefing-map"><GlobalMap alt={alt} /></section><BriefingRail alt={alt} /><section className="ref-region ref-briefing-sources"><SectionRule label="SOURCE FEED" /><SourceRow briefing alt={alt} /></section><ReferenceCore position="dock" label="BRIEFING / NEWS" subtext="I'M HERE WHEN YOU NEED ME." /></div>;
}

function TopologyMap(): ReactElement {
  const nodes = [[420, 62, "GLASGOW"], [520, 77, "EDINBURGH"], [320, 150, "BELFAST"], [478, 183, "MANCHESTER"], [598, 150, "NEWCASTLE"], [512, 250, "BIRMINGHAM"], [699, 262, "LONDON"], [286, 315, "PLYMOUTH"], [416, 332, "CARDIFF"], [585, 334, "SOUTHAMPTON"]];
  return <svg className="ref-topology-svg" viewBox="0 0 800 420" preserveAspectRatio="none" role="img" aria-label="TEST/MOCK United Kingdom system topology map"><defs><linearGradient id="ref-topology-bg" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#173d56" /><stop offset="1" stopColor="#06131f" /></linearGradient><pattern id="ref-topology-grid" width="26" height="26" patternUnits="userSpaceOnUse"><path d="M26 0H0V26" fill="none" stroke="#6ad2ea" strokeOpacity=".1" /></pattern></defs><rect width="800" height="420" fill="url(#ref-topology-bg)" /><rect width="800" height="420" fill="url(#ref-topology-grid)" /><path d="M46 92 146 58 248 68 319 40 391 56 462 38 530 71 608 50 694 84 754 150 719 231 751 296 696 344 626 359 558 400 487 380 410 405 342 362 267 372 192 336 104 355 49 298Z" fill="#123044" fillOpacity=".72" stroke="#72c8df" strokeOpacity=".32" />{nodes.map(([x, y, label], index) => <g key={label as string} className={index === 6 ? "ref-topology-node ref-topology-node--hot" : "ref-topology-node"}><circle cx={x as number} cy={y as number} r={index === 6 ? 10 : 5} /><circle cx={x as number} cy={y as number} r="2" /><text x={(x as number) + 11} y={(y as number) + 4}>{label as string}</text></g>)}<path className="ref-topology-link" d="M420 62 520 77 598 150 512 250 699 262 585 334 416 332 286 315 320 150 420 62" /><path className="ref-topology-link ref-topology-link--faint" d="M320 150 478 183 512 250M520 77 478 183 420 62" /></svg>;
}

function ProgressRing({ value, tone = "cyan" }: { value: number; tone?: "cyan" | "amber" }): ReactElement {
  const circumference = 2 * Math.PI * 20;
  return <span className={`ref-progress-ring ref-progress-ring--${tone}`} style={{ "--progress": `${value * circumference}` } as CSSProperties}><svg viewBox="0 0 48 48"><circle className="ref-progress-ring__track" cx="24" cy="24" r="20" /><circle className="ref-progress-ring__value" cx="24" cy="24" r="20" /></svg><b>{Math.round(value * 100)}%</b></span>;
}

function TaskStatus(): ReactElement {
  const tasks = [["DATA INGESTION", "Real-time feed capture", .98], ["SENSOR CALIBRATION", "Thermal / Optical / Audio", .76], ["MODEL TRAINING", "v2.7 · Pattern Recognition", .63], ["BACKUP SYNCHRONIZATION", "Offsite secure mirror", 0]];
  return <ReferencePanel eyebrow="TASK STATUS" title="" className="ref-system-tasks"><span className="ref-panel__subline">ACTIVE OPERATIONS</span>{tasks.map(([title, subtitle, progress]) => <div className="ref-task-row" key={title as string}><i>{title === "BACKUP SYNCHRONIZATION" ? "▤" : "⌾"}</i><div><strong>{title as string}</strong><span>{subtitle as string}</span></div><em>{progress === 0 ? "QUEUED" : "RUNNING"}<small>{progress === 0 ? "" : ` ${Math.round((progress as number) * 100)}%`}</small></em><ProgressRing value={progress as number} /></div>)}</ReferencePanel>;
}

function ProcessTable(): ReactElement {
  const rows = [["charlie_core.service", "8821", "RUNNING", "2h 13m"], ["sensor_hub.service", "6712", "RUNNING", "1h 48m"], ["data_ingest.service", "4921", "RUNNING", "3h 02m"], ["ml_training.worker", "9132", "RUNNING", "0h 47m"], ["sync_manager.service", "5621", "IDLE", "0h 05m"]];
  return <ReferencePanel eyebrow="WHAT IS RUNNING" title="" className="ref-process-table"><span className="ref-panel__subline">LIVE PROCESSES</span><table><thead><tr><th>PROCESS</th><th>PID</th><th>STATUS</th><th>UPTIME</th></tr></thead><tbody>{rows.map(([name, pid, status, uptime]) => <tr key={name}><td>{name}</td><td>{pid}</td><td><i className={status === "IDLE" ? "is-idle" : ""} />{status}</td><td>{uptime}</td></tr>)}</tbody></table></ReferencePanel>;
}

function SystemVitals(): ReactElement {
  const gauges = [["CPU", .32], ["MEMORY", .61], ["DISK", .48], ["NETWORK", .22]];
  return <ReferencePanel eyebrow="SYSTEM STATUS" title="" className="ref-system-vitals"><span className="ref-panel__subline">VITALS OVERVIEW</span><div className="ref-gauges">{gauges.map(([label, value]) => <div key={label as string}><ProgressRing value={value as number} /><span>{label as string}<strong>{Math.round((value as number) * 100)}%</strong></span></div>)}</div><dl className="ref-system-stats"><dt>SYSTEM TEMP</dt><dd>42°C</dd><dt>GPU TEMP</dt><dd>38°C</dd><dt>FAN SPEED</dt><dd>1270 RPM</dd><dt>POWER DRAW</dt><dd>65 W</dd><dt>UPTIME</dt><dd>3h 21m</dd></dl></ReferencePanel>;
}

function ActivityFeed(): ReactElement {
  const logs = [["10:42:11", "[INFO]", "Data ingestion stream_01 connected"], ["10:42:09", "[INFO]", "Sensor calibration complete"], ["10:42:07", "[INFO]", "Model training epoch 12/50"], ["10:41:59", "[WARN]", "High memory usage detected"], ["10:41:55", "[INFO]", "Backup sync queued"], ["10:41:52", "[INFO]", "User command received: /status"], ["10:41:49", "[INFO]", "System temperature nominal"], ["10:41:46", "[INFO]", "New device registered"]];
  return <ReferencePanel eyebrow="ACTIVITY FEED" title="" className="ref-activity-feed"><span className="ref-panel__subline">SYSTEM LOGS</span>{logs.map(([time, level, message]) => <p key={`${time}-${message}`}><time>{time}</time><b className={level === "[WARN]" ? "is-warning" : ""}>{level}</b><span>{message}</span></p>)}</ReferencePanel>;
}

function SystemTasksScreen(): ReactElement {
  return <div className="ref-screen ref-system" data-reference-screen="ref-system-tasks"><WorkspaceHeading eyebrow="SYSTEM / TASKS WORKSPACE" title="" subtitle="OVERVIEW" className="ref-system-heading" /><ReferencePanel eyebrow="NETWORK OVERVIEW" title="" className="ref-topology"><TopologyMap /></ReferencePanel><TaskStatus /><ProcessTable /><SystemVitals /><ActivityFeed /><ReferenceCore position="dock" label="IDLE" subtext="I'M HERE WHEN YOU NEED ME." /></div>;
}

function ActiveDockedScreen(): ReactElement {
  return <div className="ref-screen ref-active-docked" data-reference-screen="ref-active-docked"><WorkspaceHeading eyebrow="ACTIVE-WORK DOCKED-CORE" title="" subtitle="SYSTEM LINK ESTABLISHED" /><ReferenceCore position="dock" label="ACTIVE" subtext="I'M HERE WHEN YOU NEED ME." /></div>;
}

function ApprovalScreen(): ReactElement {
  const impact = [["⚓", "DISRUPT ILLEGAL ACTIVITY", "Prevent transfer of restricted goods."], ["♢", "STRENGTHEN MARITIME SECURITY", "Demonstrate presence in high-risk corridor."], ["▥", "INTELLIGENCE VALUE", "High-value data collection opportunity."]];
  const risks = [["△", "ESCALATION RISK", "Potential hostile response from vessel."], ["△", "COLLATERAL IMPACT", "Civilian vessels in proximity."], ["△", "POLITICAL SENSITIVITY", "Increased regional tension possible."]];
  return <div className="ref-screen ref-approval" data-reference-screen="ref-approval"><WorkspaceHeading eyebrow="OPERATOR / APPROVAL" title="" subtitle="PENDING DECISION" /><div className="ref-approval-ref">&gt; REF OPS_2025_05_21_0017<br /><small>AWAITING OPERATOR INPUT</small></div><ReferencePanel eyebrow="ACTION REQUEST" title="INTERCEPT SUSPICIOUS VESSEL" className="ref-approval-request"><div className="ref-approval-subtitle">DEPLOY MARITIME INTERDICTION UNIT</div><div className="ref-risk"><span>RISK LEVEL</span><i /><strong>HIGH</strong></div><SectionRule label="SUMMARY" /><p className="ref-approval-summary">Intelligence and sensor data indicate a high probability that the vessel MV KESTREL is involved in illicit cargo movement through a restricted maritime zone. Authorize interception to confirm cargo, detain if necessary, and divert to a designated port.</p><div className="ref-approval-columns"><div><SectionRule label="EXPECTED IMPACT" />{impact.map(([icon, title, detail]) => <div className="ref-approval-item" key={title}><i>{icon}</i><span><strong>{title}</strong><small>{detail}</small></span></div>)}</div><div><SectionRule label="POTENTIAL RISKS" />{risks.map(([icon, title, detail]) => <div className="ref-approval-item" key={title}><i className="is-risk">{icon}</i><span><strong>{title}</strong><small>{detail}</small></span></div>)}</div></div></ReferencePanel><ReferencePanel eyebrow="TARGET OVERVIEW" title="MV KESTREL" className="ref-approval-target"><div className="ref-approval-image"><SatelliteScene /></div><span className="ref-geo">51.8321 N<br />1.2748 E</span><SectionRule label="ROUTE & CONTEXT" /><MiniRouteMap /><small className="ref-approval-caption">EST. INTERCEPT<br />47 MIN</small></ReferencePanel><section className="ref-operator-decision"><SectionRule label="OPERATOR DECISION" /><div><button type="button" className="ref-decision ref-decision--approve"><i>✓</i><span>APPROVE OPERATION<small>AUTHORIZE INTERCEPTION</small></span><b>→</b></button><button type="button" className="ref-decision ref-decision--reject"><i>×</i><span>REJECT OPERATION<small>CANCEL AND STAND DOWN</small></span><b>→</b></button></div><label>NOTE (OPTIONAL)<textarea placeholder="Add operator notes..." maxLength={280} /></label></section><ReferenceCore position="dock" label="IDLE" subtext="I'M HERE WHEN YOU NEED ME." /></div>;
}

function Toggle({ checked = true }: { checked?: boolean }): ReactElement {
  return <span className={`ref-toggle ${checked ? "is-on" : ""}`} aria-hidden="true"><i /></span>;
}

function SettingsScreen(): ReactElement {
  const config = [["RESPONSE MODE", "Tone and level of autonomy", "ASSIST"], ["ANALYSIS DEPTH", "Balance speed vs. thoroughness", "BALANCED"], ["CONTEXT MEMORY", "Session and cross-domain recall", "ENABLED"], ["PROACTIVE ALERTS", "Surface risks and anomalies", "ENABLED"], ["COMMUNICATION STYLE", "Technical and concise", "TECHNICAL"], ["DOMAIN FOCUS", "Primary operational domains", "MARITIME"]];
  const safety = [["RISK THRESHOLD", "Alert sensitivity level", "MEDIUM"], ["RESTRICTED TOPICS", "Sensitive content filtering", "ENABLED"], ["AUDIT LOGGING", "Log interactions and decisions", "ENABLED"], ["HUMAN OVERSIGHT", "Require confirmation for high-risk actions", "ENABLED"]];
  const prefs = [["THEME", "Visual appearance", "DARK"], ["UNITS", "Measurement system", "METRIC"], ["TIMEZONE", "Display and reporting", "UTC"], ["SHORTCUTS", "Keyboard interactions", "STANDARD"]];
  return <div className="ref-screen ref-settings" data-reference-screen="ref-settings"><WorkspaceHeading eyebrow="SYSTEM / SETTINGS" title="" subtitle="CONFIGURE · MONITOR · CONTROL" /><nav className="ref-settings-nav" aria-label="Settings categories">{[["CHARLIE", "AI CONFIGURATION"], ["SENSORS", "DATA SOURCES"], ["NETWORK", "CONNECTIONS"], ["SECURITY", "ACCESS & PRIVACY"], ["DISPLAY", "INTERFACE & VISUALS"], ["SYSTEM", "MAINTENANCE"]].map(([label, detail], index) => <button type="button" className={index === 0 ? "is-selected" : ""} key={label}><i /> <span>{label}<small>{detail}</small></span></button>)}</nav><ReferencePanel eyebrow="CHARLIE CONFIGURATION" title="" className="ref-settings-config"><span className="ref-panel__subline">CORE BEHAVIOR & CAPABILITIES</span>{config.map(([label, detail, value], index) => <div className="ref-setting-row" key={label}><i>{["⌁", "◉", "⌘", "♢", "▣", "◎"][index]}</i><span><strong>{label}</strong><small>{detail}</small></span>{index === 2 || index === 3 ? <><em>{value}</em><Toggle /></> : <select defaultValue={value} aria-label={label}><option>{value}</option></select>}</div>)}</ReferencePanel><ReferencePanel eyebrow="SAFETY & CONSTRAINTS" title="" className="ref-settings-safety"><span className="ref-panel__subline">OPERATIONAL BOUNDARIES</span>{safety.map(([label, detail, value], index) => <div className="ref-setting-row" key={label}><i>{["△", "♙", "▤", "♧"][index]}</i><span><strong>{label}</strong><small>{detail}</small></span>{index === 0 ? <><span className="ref-slider"><i /></span><em>{value}</em></> : <><em>{value}</em><Toggle /></>}</div>)}</ReferencePanel><ReferencePanel eyebrow="SYSTEM PREFERENCES" title="" className="ref-settings-prefs"><span className="ref-panel__subline">INTERFACE & OPERATIONS</span>{prefs.map(([label, detail, value], index) => <div className="ref-setting-row" key={label}><i>{["☼", "◎", "◷", "⌨"][index]}</i><span><strong>{label}</strong><small>{detail}</small></span><select defaultValue={value} aria-label={label}><option>{value}</option></select></div>)}</ReferencePanel><ReferencePanel eyebrow="CHARLIE STATUS" title="" className="ref-settings-status"><span className="ref-panel__subline">AI SYSTEM OVERVIEW</span>{[["98%", "OPERATIONAL", "Core systems online"], ["12ms", "RESPONSE LATENCY", "Average inference time"], ["47GB", "CONTEXT MEMORY", "Allocated / 64 GB"]].map(([value, label, detail]) => <div className="ref-status-metric" key={label}><strong>{value}</strong><span><b>{label}</b><small>{detail}</small></span></div>)}<SectionRule label="ACTIVE MODULES" />{["Pattern Recognition", "Anomaly Detection", "Predictive Analysis", "Natural Language", "Maritime Intelligence"].map((module, index) => <p className="ref-module" key={module}><i />{module}<span>v{["2.7", "1.9", "2.3", "3.1", "2.0"][index]}</span></p>)}</ReferencePanel><ReferenceCore position="dock" label="ACTIVE" subtext="I'M HERE WHEN YOU NEED ME." /></div>;
}

function OnlineScreen(): ReactElement {
  return <div className="ref-screen ref-online" data-reference-screen="ref-online"><ReferenceCore position="center" label="ONLINE" subtext="I'M HERE WHEN YOU NEED ME." /><ReferencePanel eyebrow="SYSTEM" title="CPU USAGE" className="ref-online-system"><div className="ref-online-metric">18<span>%</span></div><MiniChart variant="cpu" /><dl><dt>Core Temperature</dt><dd>43°C</dd><dt>Fan Speed</dt><dd>916 RPM</dd></dl></ReferencePanel><div className="ref-online-prompt">Task understood.<br />Standing by.</div></div>;
}

function IdleScreen(): ReactElement {
  return <div className="ref-screen ref-idle" data-reference-screen="ref-idle"><ReferenceCore position="center" label="IDLE" subtext="I'M HERE WHEN YOU NEED ME." /></div>;
}

function FaultScreen(): ReactElement {
  return <div className="ref-screen ref-fault" data-reference-screen="ref-fault"><ReferenceCore position="center" label="SYSTEM FAULT" subtext="CORE SERVICES UNAVAILABLE" tone="fault" /><div className="ref-fault-detail"><span>ERR_CORE_0413</span><small>AI PROCESSOR NODE NOT RESPONDING</small></div><button type="button" className="ref-recovery-action"><i>↻</i><span>RETRY CONNECTION<small>Press R to retry or check system status.</small></span></button></div>;
}

function DegradedScreen(): ReactElement {
  const health = [["CORE PROCESSING", "72%", "DEGRADED", "cyan"], ["SENSOR INPUTS", "100%", "ONLINE", "cyan"], ["NETWORK LINK", "95%", "ONLINE", "cyan"], ["DATA ACCESS", "48%", "DEGRADED", "amber"], ["MODEL SERVICES", "100%", "ONLINE", "cyan"], ["BACKUP SYNC", "--", "OFFLINE", "amber"]];
  const limits = [["REDUCED DATA ACCESS", "Historical archives temporarily unavailable."], ["COMPUTE CAPACITY LIMITED", "Response times may be slower than normal."], ["BACKUP SYNC OFFLINE", "Offsite synchronization unavailable."]];
  return <div className="ref-screen ref-degraded" data-reference-screen="ref-degraded"><WorkspaceHeading eyebrow="SYSTEM STATUS" title="" subtitle="PARTIAL CAPABILITY" /><ReferencePanel eyebrow="SUBSYSTEM HEALTH" title="" className="ref-degraded-health"><span className="ref-panel__subline">CORE SYSTEMS OVERVIEW</span>{health.map(([label, value, status, tone]) => <div className="ref-health-row" key={label}><i className={tone === "amber" ? "is-amber" : ""}>◎</i><span>{label}</span><b><em style={{ width: value === "--" ? "0%" : value } as CSSProperties} className={tone === "amber" ? "is-amber" : ""} /></b><strong>{value}</strong><small className={tone === "amber" ? "is-amber" : ""}>{status}</small></div>)}</ReferencePanel><ReferencePanel eyebrow="CURRENT LIMITATIONS" title="" className="ref-degraded-limits">{limits.map(([title, detail], index) => <div className="ref-limit-row" key={title}><i>{["▤", "▦", "⌁"][index]}</i><span><strong>{title}</strong><small>{detail}</small></span></div>)}</ReferencePanel><ReferencePanel eyebrow="SYSTEM MESSAGE" title="" className="ref-degraded-message"><span className="ref-panel__subline">STATUS UPDATE</span><p><time>14:27:11</time> <b>[WARN]</b> Elevated error rates detected in data access service. Core functions remain operational. Continuing in degraded mode.</p></ReferencePanel><ReferenceCore position="center" label="DEGRADED MODE" subtext="I'M HERE, WITH LIMITED CAPABILITY." tone="amber" /></div>;
}

function ReferenceScreen({ scenario }: { scenario: ReferenceVisualScenario }): ReactElement {
  switch (scenario) {
    case "ref-idle": return <IdleScreen />;
    case "ref-online": return <OnlineScreen />;
    case "ref-research": return <ResearchScreen screenId={scenario} />;
    case "ref-research-selected": return <ResearchScreen selected screenId={scenario} />;
    case "ref-vision": return <VisionScreen screenId={scenario} />;
    case "ref-vision-selected": return <VisionScreen selected screenId={scenario} />;
    case "ref-briefing": return <BriefingScreen screenId={scenario} />;
    case "ref-briefing-alt": return <BriefingScreen alt screenId={scenario} />;
    case "ref-system-tasks": return <SystemTasksScreen />;
    case "ref-active-docked": return <ActiveDockedScreen />;
    case "ref-approval": return <ApprovalScreen />;
    case "ref-settings": return <SettingsScreen />;
    case "ref-fault": return <FaultScreen />;
    case "ref-degraded": return <DegradedScreen />;
  }
}

function isCenteredScenario(scenario: ReferenceVisualScenario): boolean {
  return scenario === "ref-idle" || scenario === "ref-online" || scenario === "ref-fault" || scenario === "ref-degraded";
}

export function ReferenceVisualLab({ scenario }: ReferenceVisualLabProps): ReactElement {
  const centered = isCenteredScenario(scenario);
  const position = centered ? "center" : "dock_bottom_right";
  return (
    <main
      className={`charlie-scene-root reference-lab reference-lab--${centered ? "centered" : "workspace"}`}
      data-visual-lab="TEST/MOCK"
      data-reference-lab="TEST/MOCK"
      data-reference-scenario={scenario}
      data-core-position={position}
      aria-label={`TEST/MOCK reference visual scenario ${scenario}`}
    >
      <EnvironmentLayer corePosition={centered ? "center" : "dock_bottom_right"} hasWorkspace={!centered} />
      <div className="reference-lab__content"><ReferenceScreen scenario={scenario} /></div>
      <div className="reference-proof-badge">TEST/MOCK — NOT RUNTIME ACCEPTANCE</div>
      <div className="sr-only">TEST/MOCK reference-locked visual lab — not runtime acceptance</div>
    </main>
  );
}
