import type { ProjectionData } from "./components/Projection";
import type { CoreState } from "./components/CharlieCore";
import { ChatWidget, MediaWidget, TaskWidget } from "./components/RuntimeWidgets";
import { ResearchWorkspace } from "./components/ResearchWorkspace";
import { HudChart, HudEvidence, HudImageFrame, HudMetric, HudSource, HudStatus, HudTerminal } from "./components/HudPrimitives";
import type { RuntimeResearch, RuntimeTask } from "./runtime/types";

export const fixtureNames = [
  "idle", "small", "chat", "media", "research", "research-starting", "research-planning",
  "research-searching", "research-reading", "research-synthesis", "research-complete", "research-failed",
  "system", "task", "terminal", "vision", "sources", "map", "multitasking",
] as const;
export type FixtureName = typeof fixtureNames[number];

export function fixtureFromSearch(search: string): FixtureName {
  const value = new URLSearchParams(search).get("fixture");
  return fixtureNames.find(name => name === value) ?? "idle";
}

export interface FixtureActions {
  expanded: boolean;
  extraTask: boolean;
  approved: boolean;
  paired?: boolean;
  expand: () => void;
  toggleTask: () => void;
  approve: () => void;
}

const fixtureArt = `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">
  <defs><linearGradient id="cover" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#102e3c"/><stop offset=".55" stop-color="#0b1728"/><stop offset="1" stop-color="#050b13"/></linearGradient></defs>
  <rect width="96" height="96" fill="url(#cover)"/>
  <circle cx="73" cy="22" r="22" fill="#76dff41c"/>
  <path d="M-8 78L28 43l18 14 29-35 29 25v57H-8Z" fill="#173d4e" opacity=".9"/>
  <path d="M-8 78L28 43l18 14 29-35" fill="none" stroke="#76dff4" stroke-width="2.2" opacity=".9"/>
  <path d="M8 78h80M16 86h64" fill="none" stroke="#b9f8e7" stroke-width="1" opacity=".65"/>
  <path d="M12 13h24M12 18h12" stroke="#b9f8e7" stroke-width="1" opacity=".7"/>
  <rect x="6" y="6" width="84" height="84" fill="none" stroke="#76dff466"/>
</svg>`)}`;

function WalkMap() {
  return (
    <figure className="map-content">
      <svg viewBox="0 0 720 380" width="720" height="380" role="img"
        aria-label="Illustrative local walking map: a riverside route from the station to a footbridge and gardens">
        <defs>
          <pattern id="map-blocks" width="88" height="68" patternUnits="userSpaceOnUse">
            <rect x="10" y="10" width="59" height="43" rx="3" fill="#19313b" />
            <path d="M0 0H88M0 0V68" stroke="#56717a" strokeWidth="2" opacity=".4" />
          </pattern>
        </defs>
        <rect width="720" height="380" fill="#10232b" />
        <rect width="720" height="380" fill="url(#map-blocks)" opacity=".65" />
        <path d="M270 -20C215 100 500 140 375 270S260 360 290 400"
          fill="none" stroke="#07151c" strokeWidth="87" />
        <path d="M270 -20C215 100 500 140 375 270S260 360 290 400"
          fill="none" stroke="#286477" strokeWidth="59" />
        <path d="M430 47L629 29L679 152L492 183Z" fill="#244b42" />
        <path d="M53 235L191 196L252 300L87 344Z" fill="#244b42" />
        <path d="M0 182L720 231M118 0L150 380M604 0L551 380"
          fill="none" stroke="#6d8587" strokeWidth="8" opacity=".65" />
        <path d="M344 103L415 157" stroke="#aac6c7" strokeWidth="9" />
        <path d="M141 285L174 264L241 222L299 194L344 103L415 157L490 137L544 100"
          fill="none" stroke="#9ce4d7" strokeWidth="4" strokeLinejoin="round" />
        {[{x:141,y:285},{x:344,y:103},{x:544,y:100}].map(p =>
          <g key={p.x}><circle cx={p.x} cy={p.y} r="9" fill="#0a2028" stroke="#b9f8e7" strokeWidth="2" />
            <circle cx={p.x} cy={p.y} r="3" fill="#b9f8e7" /></g>)}
        <g fill="#d0e5e4" fontSize="17" fontFamily="Geist Variable, sans-serif">
          <text x="70" y="318">Station</text><text x="281" y="78">Footbridge</text>
          <text x="481" y="73">Riverside gardens</text>
          <text x="76" y="166" fontSize="13" fill="#8ea9ac">Mill Street</text>
          <text x="445" y="308" fontSize="13" fill="#8ebfc9">River</text>
        </g>
      </svg>
      <figcaption>Illustrative walking route · local demonstration map</figcaption>
    </figure>
  );
}

function VisionFixture() {
  return <HudImageFrame>
    <div className="fixture-vision-frame">
      <svg viewBox="0 0 720 430" role="img" aria-label="Deterministic fixture camera frame with a desk and monitor">
        <defs><linearGradient id="vision-surface" x1="0" x2="1" y1="0" y2="1">
          <stop offset="0" stopColor="#142c35" /><stop offset="1" stopColor="#050d12" />
        </linearGradient></defs>
        <rect width="720" height="430" fill="url(#vision-surface)" />
        <path d="M0 324H720M80 324V430M640 324V430" stroke="#76dff44a" />
        <rect x="184" y="78" width="352" height="192" fill="#071117" stroke="#8edce98a" />
        <rect x="207" y="100" width="306" height="142" fill="#0b222b" stroke="#4c94a16e" />
        <path d="M244 210h232M324 270v31h72v-31" fill="none" stroke="#8edce966" />
        <path d="M36 54h92M36 54v34M684 54h-92M684 54v34" fill="none" stroke="#b9e9f076" />
        <circle cx="360" cy="171" r="26" fill="none" stroke="#76dff4" strokeWidth="2" />
        <path d="M360 132v-12M360 210v12M321 171h-12M399 171h12" stroke="#76dff4" />
      </svg>
      <span className="fixture-vision-reticle" aria-hidden="true" />
    </div>
  </HudImageFrame>;
}

const researchQuestion = "Which configuration change should we make before updating the dependency?";
const researchSources = [
  { id: "S1", title: "Migration guide", domain: "docs.example.test", url: "https://example.test/migration", snippet: "The timeout key moved into client options." },
  { id: "S2", title: "Release notes", domain: "releases.example.test", url: "https://example.test/release-notes", snippet: "The legacy configuration key is deprecated." },
  { id: "S3", title: "Request tests", domain: "tests.example.test", url: "https://example.test/tests", snippet: "The slow-response behavior remains covered by existing tests." },
];
const researchFindings = [
  { id: "F1", title: "Move the timeout key into client options.", detail: "The migration guide describes the new location and the release notes mark the old key deprecated.", source_ids: ["S1", "S2"] },
  { id: "F2", title: "Keep the current package version until verification.", detail: "Existing request tests cover the behavior that must remain stable after the configuration-only change.", source_ids: ["S3"] },
];
const researchPlan = [
  { id: "P1", title: "Inspect the migration guidance", status: "complete" },
  { id: "P2", title: "Compare release notes and current configuration", status: "complete" },
  { id: "P3", title: "Verify request behavior before closing", status: "active" },
];

function fixtureResearch(name: FixtureName): RuntimeResearch {
  const stage = name === "research" ? "research-complete" : name;
  const base = {
    schema: "charlie.research_workspace" as const,
    version: 1 as const,
    query: researchQuestion,
    objective: researchQuestion,
    mode: "standard",
    title: "Research & Synthesis",
    plan: researchPlan,
    sources: researchSources,
    findings: researchFindings,
    confidence: 0.82,
    timeline_items: [],
  };
  const activity = (items: Array<[string, string]>): RuntimeResearch["activity"] =>
    items.map(([itemStage, message], index) => ({ id: `fixture-activity-${index}`, stage: itemStage, message }));
  if (stage === "research-starting") return {
    progress: { stage: "accepted", message: "Research request accepted. Preparing source plan.", mode: "standard" },
    result: null, objective: researchQuestion, activity: activity([["accepted", "Research request accepted."]]), truth: "FIXTURE",
  };
  if (stage === "research-planning") return {
    progress: { stage: "planning", message: "Building subquestions from the request.", mode: "standard" },
    result: { ...base, summary: "", status: "planning", findings: [], sources: [] }, objective: researchQuestion,
    activity: activity([["accepted", "Research request accepted."], ["planning", "Building subquestions from the request."]]), truth: "FIXTURE",
  };
  if (stage === "research-searching") return {
    progress: { stage: "searching", message: "Running the planned searches.", current: 1, total: 3, mode: "standard" },
    result: null, objective: researchQuestion,
    activity: activity([["planning", "Plan ready."], ["searching", "Running the planned searches."]]), truth: "FIXTURE",
  };
  if (stage === "research-reading") return {
    progress: { stage: "reading", message: "Reading source evidence and linking claims.", current: 2, total: 3, mode: "standard" },
    result: { ...base, summary: "Evidence is being linked to the active findings.", status: "reading", findings: [researchFindings[0]], sources: researchSources.slice(0, 2) }, objective: researchQuestion,
    activity: activity([["searching", "Three candidate sources returned."], ["reading", "Reading migration guidance and release notes."]]), truth: "FIXTURE",
  };
  if (stage === "research-synthesis") return {
    progress: { stage: "synthesizing", message: "Comparing evidence before forming the answer.", current: 3, total: 3, mode: "standard" },
    result: { ...base, summary: "The evidence points to a configuration-only change.", status: "synthesizing" }, objective: researchQuestion,
    activity: activity([["reading", "Evidence linked to two findings."], ["synthesizing", "Comparing source agreement."]]), truth: "FIXTURE",
  };
  if (stage === "research-failed") return {
    progress: null,
    result: { ...base, summary: "Research could not complete because source evidence was unavailable.", status: "error", findings: [], sources: [], stop_reason: "provider-unavailable" },
    objective: researchQuestion, activity: activity([["failed", "Source provider did not return usable evidence."]]), truth: "FIXTURE",
  };
  return {
    progress: null,
    result: { ...base, summary: "Update the configuration before changing the dependency.", status: "complete" },
    objective: researchQuestion, activity: activity([["complete", "Answer synthesized from three linked sources."]]), truth: "FIXTURE",
  };
}

export function makeFixture(name: FixtureName, actions: FixtureActions): {
  items: ProjectionData[]; state: CoreState;
} {
  const finding: ProjectionData = {
    id: "finding", importance: 90, depth: "active", treatment: "structured",
    label: "Investigation / dependency review", heading: "The update needs one configuration change.",
    content: <div className="narrative">
      <p>The sample service still uses the legacy timeout key. Rename it before updating
        the dependency, then run the existing request tests.</p>
      <p className="finding-note">Scope: configuration only. No application logic needs to change.</p>
      {actions.expanded && <div className="expanded-finding">
        <p>The migration guide moves the timeout setting into the client options. Leaving
          the old key in place would silently use the default, changing how long requests wait.</p>
        <p>Recommended sequence: update the setting, verify the slow-request test, and review
          the lockfile diff. Keep the current package version until those checks pass.</p>
      </div>}
      <button className="text-action" onClick={actions.expand}>
        {actions.expanded ? "Show less detail" : "Explain the change"} <span aria-hidden="true">↗</span>
      </button>
    </div>,
  };
  const evidence: ProjectionData = {
    id: "evidence", importance: 45, relatedTo: "finding", depth: "active",
    treatment: "structured", label: "Evidence", heading: "Three references agree",
    content: <ol className="evidence-list">
      <li><span className="source-number">01</span><div><strong>Migration guide</strong><p>Timeout key moved to client options.</p></div></li>
      <li><span className="source-number">02</span><div><strong>Release notes</strong><p>Legacy configuration is deprecated.</p></div></li>
      <li><span className="source-number">03</span><div><strong>Local test output</strong><p>Request behavior still needs verification.</p></div></li>
    </ol>,
  };
  const command: ProjectionData = {
    id: "command", importance: 40, relatedTo: "finding", depth: "active",
    treatment: "hard", label: "Running command / sample", heading: "Checking request behavior",
    content: <><pre><code>$ npm test -- requests{"\n"}RUN  request-timeout.test.ts{"\n"}Waiting for slow-response case…</code></pre>
      <p className="status-line"><span className="status-dot" />12.8s elapsed · sample output</p></>,
  };
  const background: ProjectionData = {
    id: "background", importance: 10, depth: "background", treatment: "free",
    label: "In the background", content: <><p className="status-summary">2 tasks running</p>
      <p className="metadata">Indexing notes · checking containers</p>
      <button className="text-action" onClick={actions.toggleTask}>{actions.extraTask ? "Remove sample task" : "Add sample task"}</button></>,
  };
  const approval: ProjectionData = {
    id: "approval", importance: 90, interactionPriority: 30, relatedTo: "finding",
    depth: "priority", treatment: "hard", label: "Permission required", heading: "Update the timeout setting?",
    content: <><p className="file-path">src/config.ts</p><p>Allow Charlie to modify this file?</p>
      <div className="actions"><button onClick={actions.approve}>Allow in demo</button>
        <span className="metadata">Local simulation · no file write</span></div></>,
  };
  let items: ProjectionData[] = [];
  let state: CoreState = "idle";
  if (name === "small") {
    state = "speaking";
    items = [{ id: "answer", importance: 30, treatment: "free", depth: "active",
      content: <p className="small-answer">The notes are ready.<br /><span>Three decisions, all in one place.</span></p> }];
  }
  if (name === "multitasking") {
    state = "thinking";
    items = [finding, evidence, background];
    if (name === "multitasking") {
      items.push(command);
      state = actions.approved ? "acting" : "waiting-for-approval";
      if (!actions.approved) items.push(approval);
    }
    if (actions.extraTask) items.push({
      id: "extra-task", importance: 5, relatedTo: "background", depth: "background",
      treatment: "free", content: <p className="status-line"><span className="status-dot" />Exporting summary · queued</p>,
    });
  }
  if (name === "media") {
    state = "acting";
    const mediaItem: ProjectionData = {
      id: "media-widget", importance: 74, relatedTo: "charlie", preferredZone: "around-core", draggable: true,
      depth: "active", treatment: "free", label: "Visual fixture / media", content: <MediaWidget
        media={{ available: true, title: "Night Drive", artist: "Fixture Session", status: "playing",
          position_seconds: 42, duration_seconds: 214, art_uri: fixtureArt }} onControl={() => undefined} localControls />,
    };
    const chatItem: ProjectionData = {
      id: "chat-widget", importance: 86, relatedTo: "charlie", preferredZone: "around-core", draggable: true,
      depth: "active", treatment: "free", label: "Visual fixture / conversation", content: <ChatWidget
        conversation={{ userText: "Summarize the local notes", responseText: "Three decisions are ready in one place.", streaming: false, turnId: "fixture-pair" }}
        thinking="" onSend={() => true} />,
    };
    items = actions.paired ? [chatItem, mediaItem] : [mediaItem];
  }
  if (name === "chat") {
    state = "speaking";
    items = [{ id: "chat-widget", importance: 86, relatedTo: "charlie", preferredZone: "around-core", draggable: true,
      depth: "active", treatment: "free", label: "Visual fixture / conversation", content: <ChatWidget
        conversation={{ userText: "Summarize the local notes", responseText: "Three decisions are ready in one place.", streaming: false, turnId: "fixture-chat" }}
        thinking="" onSend={() => true} /> }];
  }
  if (name === "research" || name.startsWith("research-")) {
    const research = fixtureResearch(name);
    const compact = ["research-starting", "research-planning", "research-searching", "research-failed"].includes(name);
    state = name === "research-failed" ? "degraded" : research.progress ? "thinking" : "speaking";
    items = [{ id: "research-surface", importance: compact ? 76 : 98, immersive: !compact,
      preferredZone: compact ? "around-core" : "center", relatedTo: compact ? "charlie" : undefined,
      depth: "active", treatment: compact ? "free" : "integrated", label: "FIXTURE / RESEARCH", draggable: false,
      content: <ResearchWorkspace research={research} compact={compact} truth="FIXTURE" /> }];
  }
  if (name === "system") {
    state = "idle";
    items = [{ id: "system-widget", importance: 72, relatedTo: "charlie", preferredZone: "around-core", draggable: true,
      depth: "active", treatment: "free", label: "Visual fixture / system", heading: "Host health",
      content: <div className="fixture-system-surface"><div className="fixture-metrics">
        <HudMetric label="CPU" value="18.4" unit="%" /><HudMetric label="MEMORY" value="56.1" unit="%" />
        <HudMetric label="DISK" value="42.8" unit="%" /></div>
        <HudChart values={[32, 38, 35, 48, 44, 56, 51]} label="System load trend" />
        <HudStatus>All fixture services responding</HudStatus>
      </div> }];
  }
  if (name === "task") {
    state = "acting";
    const tasks: RuntimeTask[] = [
      { id: "fixture-task-1", title: "Indexing notes", status: "running", current_action: "Reading" },
      { id: "fixture-task-2", title: "Checking containers", status: "verifying" },
    ];
    items = [{ id: "task-widget", importance: 62, relatedTo: "charlie", preferredZone: "around-core", draggable: true,
      depth: "active", treatment: "free", label: "Visual fixture / tasks", content: <TaskWidget tasks={tasks} /> }];
  }
  if (name === "terminal") {
    state = "acting";
    items = [{ id: "terminal-surface", importance: 90, immersive: true, preferredZone: "center", depth: "active",
      treatment: "hard", label: "Visual fixture / terminal", heading: "Request verification",
      content: <><HudStatus>Process running · 12.8s elapsed</HudStatus>
        <HudTerminal>{"PS C:\\Projects\\charlie> npm test -- requests\nRUN  request-timeout.test.ts\nWaiting for slow-response case…"}</HudTerminal></> }];
  }
  if (name === "vision") {
    state = "acting";
    items = [{ id: "vision-surface", importance: 90, immersive: true, preferredZone: "center", depth: "active",
      treatment: "integrated", label: "Visual fixture / vision", heading: "Desk observation", content: <>
        <VisionFixture /><HudStatus>Frame observed · monitor region identified</HudStatus></> }];
  }
  if (name === "sources") {
    state = "idle";
    items = [{ id: "source-widget", importance: 68, relatedTo: "charlie", preferredZone: "around-core", draggable: true,
      depth: "active", treatment: "free", label: "Visual fixture / sources", heading: "Evidence deck",
      content: <HudEvidence><HudSource number="01" title="Local notes" detail="Three decisions recorded." />
        <HudSource number="02" title="Runbook" detail="Timeout migration sequence." />
        <HudSource number="03" title="Test output" detail="Request behavior verified." /></HudEvidence> }];
  }
  if (name === "map") {
    state = "acting";
    items = [{ id: "map-surface", importance: 90, immersive: true, preferredZone: "center", depth: "active",
      treatment: "integrated", label: "Visual fixture / map", heading: "A quieter way through the city", content: <WalkMap /> }];
  }
  return { items, state };
}
