import { useEffect, useMemo, useState, type ReactElement, type ReactNode } from "react";
import { CharlieRing } from "../scene/core/CharlieRing";
import { EnvironmentLayer } from "../scene/EnvironmentLayer";
import { ChartPrimitive } from "../composer/primitives/ChartPrimitive";
import { SpatialMapPrimitive, type SpatialMapData } from "../composer/primitives/SpatialMapPrimitive";
import { useCharlieStore, type PresentationIntent, type ResearchResultPayload, type RuntimeTask, type SubsystemHealth, type SystemStatus, type ToolApprovalRequest } from "../store/charlie";
import { normalizeBriefingWorkspacePayload, normalizeResearchWorkspacePayload, type BriefingWorkspacePayload, type ResearchWorkspacePayload } from "../presentation/workspacePayloads";
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
  "ref-idle", "ref-online", "ref-research", "ref-research-selected", "ref-vision", "ref-vision-selected",
  "ref-briefing", "ref-briefing-alt", "ref-system-tasks", "ref-active-docked", "ref-approval", "ref-settings",
  "ref-fault", "ref-degraded",
] as const satisfies readonly ReferenceVisualScenario[];

type CorePosition = "center" | "dock";
type ReferenceTone = "cyan" | "amber" | "fault";
type UnavailableKind = "unavailable" | "not-wired" | "unsupported";

interface ReferenceVisualLabProps { scenario: ReferenceVisualScenario; }

interface ReferencePanelProps {
  eyebrow: string;
  title: string;
  className?: string;
  children: ReactNode;
}

interface ConfigField {
  key: string;
  field?: string;
  label: string;
  group: string;
  type: "str" | "int" | "float" | "bool";
  secret: boolean;
  restart: string | null;
  value: unknown;
  is_set: boolean | null;
}

interface RuntimeHealthResponse {
  subsystems?: Record<string, { status?: string; detail?: string }>;
}

interface SettingsSnapshot {
  fields: ConfigField[];
  health: Record<string, { status?: string; detail?: string }>;
}

interface ReferenceDataSnapshot {
  connected: boolean;
  intents: Record<string, PresentationIntent>;
  researchPayload: ResearchWorkspacePayload | null;
  researchContent: Record<string, unknown>;
  latestResearchResult: ResearchResultPayload | null;
  briefingPayload: BriefingWorkspacePayload | null;
  briefingContent: Record<string, unknown>;
  visionContent: Record<string, unknown>;
  systemContent: Record<string, unknown>;
  systemStatus: SystemStatus | null;
  subsystemHealth: Record<string, SubsystemHealth>;
  tasks: Record<string, RuntimeTask>;
  activities: string[];
  approval: ToolApprovalRequest | null;
  netHistory: number[];
  settings: SettingsSnapshot;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function text(value: unknown): string {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function displayValue(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function workspaceIntent(intents: Record<string, PresentationIntent>, workspaceType: string): PresentationIntent | null {
  return Object.values(intents).find((intent) => intent.kind === "workspace" && intent.workspaceType === workspaceType) ?? null;
}

function researchPayloadFor(intent: PresentationIntent | null): { payload: ResearchWorkspacePayload | null; content: Record<string, unknown> } {
  const content = intent?.content || {};
  const required = ["query", "mode", "summary", "status", "confidence", "findings", "sources"];
  return { payload: required.every((key) => Object.prototype.hasOwnProperty.call(content, key)) ? normalizeResearchWorkspacePayload(content) : null, content };
}

function briefingPayloadFor(intent: PresentationIntent | null): { payload: BriefingWorkspacePayload | null; content: Record<string, unknown> } {
  const content = intent?.content || {};
  const required = ["headline", "summary", "stories", "summaries", "sources", "status", "confidence"];
  return { payload: required.every((key) => Object.prototype.hasOwnProperty.call(content, key)) ? normalizeBriefingWorkspacePayload(content) : null, content };
}

function spatialDataFor(content: Record<string, unknown>, keys: string[]): SpatialMapData | null {
  for (const key of keys) {
    const candidate = record(content[key]);
    if ((Array.isArray(candidate.nodes) && candidate.nodes.length > 0) || (Array.isArray(candidate.objects) && candidate.objects.length > 0)) return candidate as SpatialMapData;
  }
  return null;
}

function chartDataFor(content: Record<string, unknown>, keys: string[]): Record<string, unknown> | null {
  for (const key of keys) {
    const candidate = record(content[key]);
    if (Array.isArray(candidate.data) && candidate.data.some((item) => number(record(item).value) !== null)) return candidate;
  }
  return null;
}

function heatmapDataFor(content: Record<string, unknown>): Record<string, unknown> | null {
  for (const key of ["heatmap", "heatmap_data", "density"]) {
    const candidate = record(content[key]);
    if (Array.isArray(candidate.points) && candidate.points.some((item) => number(record(item).value) !== null)) return candidate;
  }
  return null;
}

function useSettingsSnapshot(enabled: boolean): SettingsSnapshot {
  const [snapshot, setSnapshot] = useState<SettingsSnapshot>({ fields: [], health: {} });
  useEffect(() => {
    if (!enabled || import.meta.env.MODE === "test") return;
    let active = true;
    void Promise.all([
      fetch("/api/config").then(async (response) => response.ok ? await response.json() as { fields?: ConfigField[] } : null).catch(() => null),
      fetch("/api/health").then(async (response) => response.ok ? await response.json() as RuntimeHealthResponse : null).catch(() => null),
    ]).then(([config, health]) => {
      if (active) setSnapshot({ fields: Array.isArray(config?.fields) ? config.fields : [], health: health?.subsystems || {} });
    });
    return () => { active = false; };
  }, [enabled]);
  return snapshot;
}

function ReferencePanel({ eyebrow, title, className = "", children }: ReferencePanelProps): ReactElement {
  return <section className={`ref-panel ${className}`.trim()}><div className="ref-panel__heading"><span className="ref-eyebrow">{eyebrow}</span>{title && <h2>{title}</h2>}</div>{children}</section>;
}

function DataUnavailable({ label, kind = "unavailable" }: { label: string; kind?: UnavailableKind }): ReactElement {
  const detail = kind === "not-wired" ? "DATA CONTRACT MISSING / NOT WIRED" : kind === "unsupported" ? "UNSUPPORTED DATA SHAPE" : "REAL DATA / UNAVAILABLE STATE";
  return <div className="ref-unavailable" role="status"><strong>{label}</strong><span>{detail}</span></div>;
}

function DataPlane({ label, kind = "unavailable" }: { label: string; kind?: UnavailableKind }): ReactElement {
  return <div className="ref-data-plane"><DataUnavailable label={label} kind={kind} /></div>;
}

function ReferenceCore({ position, tone = "cyan" }: { position: CorePosition; tone?: ReferenceTone }): ReactElement {
  const connected = useCharlieStore((state) => state.connected);
  const runtime = useCharlieStore((state) => state.visualRuntime);
  return <div className={`ref-core ref-core--${position} ref-core--${tone}`} data-reference-core="authoritative-charlie-ring" data-core-position={position === "center" ? "center" : "dock_bottom_right"} data-core-tone={tone} role="img" aria-label={`C.H.A.R.L.I.E. ${connected ? runtime.label : "NOT VERIFIED"}`}><div className="ref-core__orb"><CharlieRing compact={position === "dock"} /><span className="ref-core__wordmark">C.H.A.R.L.I.E.</span></div><div className="ref-core__status"><span className="ref-core__label">{connected ? runtime.label : "NOT VERIFIED"}</span><span className="ref-core__subtext">{connected ? runtime.detail || "RUNTIME STATE OBSERVED" : "REAL DATA / UNAVAILABLE STATE"}</span><span className="ref-core__dots" aria-hidden="true"><i /><i /><i /></span></div></div>;
}

function WorkspaceHeading({ eyebrow, title, subtitle, className = "" }: { eyebrow: string; title: string; subtitle: string; className?: string }): ReactElement {
  return <header className={`ref-workspace-heading ${className}`.trim()}><span className="ref-eyebrow">{eyebrow}</span>{title && <h1>{title}</h1>}<p>{subtitle}</p></header>;
}

function SectionRule({ label }: { label: string }): ReactElement {
  return <div className="ref-section-rule"><span>{label}</span></div>;
}

function Metric({ label, value, suffix = "" }: { label: string; value: number | null; suffix?: string }): ReactElement {
  return <div className="ref-live-metric"><span>{label}</span><strong>{value === null ? "UNAVAILABLE" : `${value}${suffix}`}</strong></div>;
}

function LiveMap({ data, label }: { data: SpatialMapData | null; label: string }): ReactElement {
  return data ? <SpatialMapPrimitive data={{ ...data, useRealEngine: false }} /> : <DataPlane label={label} kind="not-wired" />;
}

function LiveChart({ data, label }: { data: Record<string, unknown> | null; label: string }): ReactElement {
  return data ? <ChartPrimitive primitive={{ id: "reference-live-chart", type: "chart", data }} /> : <DataUnavailable label={label} kind="not-wired" />;
}

function SourceList({ sources }: { sources: Array<Record<string, unknown>> }): ReactElement {
  if (!sources.length) return <DataUnavailable label="NO SOURCE EVIDENCE AVAILABLE" />;
  return <div className="ref-source-row">{sources.slice(0, 4).map((source, index) => { const title = text(source.title) || "SOURCE TITLE UNAVAILABLE"; const domain = text(source.domain ?? source.publisher) || "SOURCE IDENTITY UNAVAILABLE"; const when = text(source.published_at ?? source.timestamp ?? source.time) || "TIME UNAVAILABLE"; return <article className="ref-source-card" key={text(source.id) || `${title}-${index}`}><div className="ref-source-thumb ref-source-thumb--empty"><span>NO SOURCE IMAGE</span></div><div className="ref-source-card__body"><span>{domain}</span><strong>{title}</strong><small>{when}</small>{text(source.snippet) && <p>{text(source.snippet)}</p>}</div></article>; })}</div>;
}

function TimelineList({ items }: { items: Array<{ title: string; summary?: string; time?: string; timestamp?: string }> }): ReactElement {
  if (!items.length) return <DataUnavailable label="NO TIMELINE DATA AVAILABLE" />;
  return <div className="ref-timeline">{items.slice(0, 5).map((item, index) => <div className="ref-timeline__item" key={`${item.title}-${index}`}><i /><strong>{item.time || item.timestamp || "TIME UNAVAILABLE"}</strong><span>{item.title || "EVENT UNAVAILABLE"}{item.summary ? ` · ${item.summary}` : ""}</span></div>)}</div>;
}

function FindingsList({ payload }: { payload: ResearchWorkspacePayload | null }): ReactElement {
  if (!payload?.findings.length) return <DataUnavailable label="NO GROUNDED FINDINGS REPORTED" />;
  return <div className="ref-findings-list">{payload.findings.slice(0, 5).map((finding) => <div className="ref-finding" key={finding.id}><span className="ref-finding__icon" aria-hidden="true">{finding.contradiction ? "△" : "◈"}</span><div><strong>{finding.title || "FINDING TITLE UNAVAILABLE"}</strong><p>{finding.detail || "FINDING DETAIL UNAVAILABLE"}</p></div></div>)}</div>;
}

function Heatmap({ data }: { data: Record<string, unknown> | null }): ReactElement {
  if (!data) return <DataUnavailable label="NO ACTIVITY DENSITY DATA AVAILABLE" kind="not-wired" />;
  const points = Array.isArray(data.points) ? data.points.map(record) : [];
  const cols = Math.max(1, number(data.gridWidth) || Math.max(...points.map((point) => number(point.x) || 0), 0) + 1);
  const rows = Math.max(1, number(data.gridHeight) || Math.max(...points.map((point) => number(point.y) || 0), 0) + 1);
  return <div className="ref-live-heatmap"><div className="ref-live-heatmap__grid" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))` }}>{Array.from({ length: cols * rows }, (_, index) => { const x = index % cols; const y = Math.floor(index / cols); const point = points.find((item) => number(item.x) === x && number(item.y) === y); const value = number(point?.value) || 0; return <span key={`${x}-${y}`} style={{ opacity: .22 + value * .78, background: value > .8 ? "#dc6c5a" : value > .5 ? "#3eb5d1" : "#145879" }} />; })}</div><div className="ref-heatmap__legend"><span>{text(data.minLabel) || "LOW"}</span><i /><span>{text(data.maxLabel) || "HIGH"}</span></div></div>;
}

function ResearchScreen({ selected, screenId, payload, content, result }: { selected: boolean; screenId: string; payload: ResearchWorkspacePayload | null; content: Record<string, unknown>; result: ResearchResultPayload | null }): ReactElement {
  const map = spatialDataFor(content, ["spatial_map", "radar", "map_data", "map"]);
  const chart = chartDataFor(content, ["chart", "chart_data", "activity_history"]);
  const title = payload?.title || (result ? "RESEARCH RESULT" : "RESEARCH DATA UNAVAILABLE");
  const sources = payload?.sources as unknown as Array<Record<string, unknown>> || result?.sources || [];
  return <div className={`ref-screen ref-research ${selected ? "ref-research--selected" : ""}`} data-reference-screen={screenId}><WorkspaceHeading eyebrow="RESEARCH WORKSPACE" title={title} subtitle={payload?.mode?.toUpperCase() || result?.mode?.toUpperCase() || "REAL DATA / UNAVAILABLE STATE"} /><div className="ref-research-objective"><span>RESEARCH OBJECTIVE</span><p>{payload?.query || result?.query || "NO RESEARCH OBJECTIVE AVAILABLE"}</p></div><section className="ref-region ref-research-map"><LiveMap data={map} label="GEOGRAPHIC DATA UNAVAILABLE" /><div className="ref-map-caption">◈ REAL-TIME ACTIVITY FEED <small>{map ? "LIVE DATA" : "DATA CONTRACT MISSING / NOT WIRED"}</small></div></section><ReferencePanel eyebrow="ACTIVITY OVER TIME" title="" className="ref-research-chart"><LiveChart data={chart} label="NO ACTIVITY HISTORY AVAILABLE" /></ReferencePanel><ReferencePanel eyebrow="ACTIVITY DENSITY" title="" className="ref-research-heat"><span className="ref-panel__subline">{text(record(content.heatmap).subtitle) || "REAL DATA / UNAVAILABLE STATE"}</span><Heatmap data={heatmapDataFor(content)} /></ReferencePanel><ReferencePanel eyebrow="KEY FINDINGS" title="" className="ref-research-findings"><FindingsList payload={payload} /></ReferencePanel><section className="ref-region ref-research-sources"><SectionRule label="EVIDENCE & SOURCES" /><SourceList sources={sources} /></section><section className="ref-region ref-research-timeline"><SectionRule label="TIMELINE" /><TimelineList items={payload?.timeline_items || []} /></section>{selected && <aside className="ref-anomaly-popup" aria-label="Selected anomaly context"><span className="ref-eyebrow">SELECTED CONTEXT</span><DataUnavailable label="NO SELECTED FINDING AVAILABLE" kind="not-wired" /></aside>}<ReferenceCore position="dock" /></div>;
}

function OnlineScreen({ status, netHistory }: { status: SystemStatus | null; netHistory: number[] }): ReactElement {
  const cpu = status?.cpu ?? null;
  return <div className="ref-screen ref-online" data-reference-screen="ref-online"><ReferenceCore position="center" /><ReferencePanel eyebrow="SYSTEM" title="CPU USAGE" className="ref-online-system"><div className="ref-online-metric">{cpu === null ? "—" : cpu}<span>{cpu === null ? "" : "%"}</span></div>{netHistory.length ? <div className="ref-live-sparkline">{netHistory.slice(-24).map((value, index) => <i key={`${value}-${index}`} style={{ height: `${Math.max(5, Math.min(100, value / Math.max(...netHistory, 1) * 100))}%` }} />)}</div> : <DataUnavailable label="NO TELEMETRY HISTORY AVAILABLE" />}<dl><dt>Core Temperature</dt><dd>UNAVAILABLE</dd><dt>Fan Speed</dt><dd>UNAVAILABLE</dd></dl></ReferencePanel><div className="ref-online-prompt"><DataUnavailable label="RUNTIME STATE UNAVAILABLE" /></div></div>;
}

function VisionSurface({ content }: { content: Record<string, unknown> }): ReactElement {
  const imageUrl = text(content.image_url ?? content.snapshot_url);
  const boxes = Array.isArray(content.bounding_boxes) ? content.bounding_boxes.map(record).filter((box) => Array.isArray(box.box) && box.box.length === 4) : [];
  if (!imageUrl) return <DataPlane label="NO LIVE MEDIA FEED AVAILABLE" />;
  return <div className="ref-live-vision"><img src={imageUrl} alt="REAL DATA vision frame" />{boxes.map((box, index) => { const values = (box.box as unknown[]).map(number); if (values.some((value) => value === null)) return null; const [ymin, xmin, ymax, xmax] = values as number[]; return <div className="ref-live-vision__box" key={text(box.id) || `box-${index}`} style={{ top: `${ymin}%`, left: `${xmin}%`, width: `${xmax - xmin}%`, height: `${ymax - ymin}%` }}><span>{text(box.label) || "GROUNDED REGION"}</span></div>; })}</div>;
}

function VisionScreen({ selected, screenId, content }: { selected: boolean; screenId: string; content: Record<string, unknown> }): ReactElement {
  return <div className={`ref-screen ref-vision ${selected ? "ref-vision--selected" : ""}`} data-reference-screen={screenId}><WorkspaceHeading eyebrow="VISION / MEDIA WORKSPACE" title={text(content.title) || "VISION DATA UNAVAILABLE"} subtitle={selected ? "SELECTED TARGET ANALYSIS" : "REAL DATA / UNAVAILABLE STATE"} /><section className="ref-region ref-vision-media"><SectionRule label={selected ? "AUTHORITATIVE VISION FRAME" : "LIVE MEDIA FEED"} /><div className="ref-vision-media-frame"><VisionSurface content={content} /></div></section><ReferencePanel eyebrow={selected ? "TARGET IDENTIFICATION" : "SELECTED TARGET"} title={text(content.target_name) || "TARGET DATA UNAVAILABLE"} className="ref-target-overview"><DataUnavailable label="TARGET METADATA UNAVAILABLE" kind="not-wired" /></ReferencePanel>{selected ? <><section className="ref-media-timeline"><SectionRule label="MEDIA TIMELINE" /><DataUnavailable label="NO MEDIA TIMELINE AVAILABLE" kind="not-wired" /></section><section className="ref-attachments"><SectionRule label="EVIDENCE & ATTACHMENTS" /><DataUnavailable label="NO MEDIA ATTACHMENTS AVAILABLE" kind="not-wired" /></section></> : <section className="ref-camera-strip"><SectionRule label="CAMERA VIEWS" /><DataUnavailable label="NO CAMERA VIEW INDEX AVAILABLE" kind="not-wired" /></section>}<ReferenceCore position="dock" /></div>;
}

function BriefingRail({ payload }: { payload: BriefingWorkspacePayload | null }): ReactElement {
  return <aside className="ref-briefing-rail"><SectionRule label="BRIEFING / NEWS" /><span className="ref-eyebrow">TOP HEADLINE</span><h2>{payload?.headline || "NO BRIEFING HEADLINE AVAILABLE"}</h2><span className="ref-eyebrow">SUMMARY</span><p>{payload?.summary || "REAL DATA / UNAVAILABLE STATE"}</p><SectionRule label="KEY TIMELINE" /><TimelineList items={payload?.timeline_items || []} /></aside>;
}

function BriefingScreen({ alt, screenId, payload, content }: { alt: boolean; screenId: string; payload: BriefingWorkspacePayload | null; content: Record<string, unknown> }): ReactElement {
  const map = spatialDataFor(content, ["geo_data", "map_data", "map", "spatial_map"]);
  return <div className={`ref-screen ref-briefing ${alt ? "ref-briefing--alt" : ""}`} data-reference-screen={screenId}><div className="ref-briefing-heading"><span className="ref-eyebrow">BRIEFING / NEWS</span></div><section className="ref-region ref-briefing-map"><LiveMap data={map} label="BRIEFING GEOGRAPHIC DATA UNAVAILABLE" /></section><BriefingRail payload={payload} /><section className="ref-region ref-briefing-sources"><SectionRule label="SOURCE FEED" /><SourceList sources={payload?.sources as unknown as Array<Record<string, unknown>> || []} /></section><ReferenceCore position="dock" /></div>;
}

function TaskStatus({ tasks }: { tasks: Record<string, RuntimeTask> }): ReactElement {
  const active = Object.values(tasks).filter((task) => ["queued", "planning", "waiting", "running", "paused", "approval_required", "verifying"].includes(task.status));
  return <ReferencePanel eyebrow="TASK STATUS" title="" className="ref-system-tasks"><span className="ref-panel__subline">ACTIVE OPERATIONS</span>{active.length ? active.slice(0, 4).map((task) => <div className="ref-task-row" key={task.id}><i>⌾</i><div><strong>{task.title}</strong><span>{task.currentAction || "CURRENT ACTION UNAVAILABLE"}</span></div><em>{task.status.toUpperCase()}<small>{typeof task.progress === "number" ? `${Math.round(task.progress * 100)}%` : "PROGRESS UNAVAILABLE"}</small></em></div>) : <DataUnavailable label="NO ACTIVE OPERATIONS REPORTED" />}</ReferencePanel>;
}

function ProcessTable({ content }: { content: Record<string, unknown> }): ReactElement {
  const processes = Array.isArray(content.processes) ? content.processes.map(record) : [];
  return <ReferencePanel eyebrow="WHAT IS RUNNING" title="" className="ref-process-table"><span className="ref-panel__subline">LIVE PROCESSES</span>{processes.length ? <table><thead><tr><th>PROCESS</th><th>PID</th><th>STATUS</th><th>UPTIME</th></tr></thead><tbody>{processes.slice(0, 6).map((process, index) => <tr key={text(process.name) || `process-${index}`}><td>{text(process.name) || "PROCESS NAME UNAVAILABLE"}</td><td>{text(process.pid) || "PID UNAVAILABLE"}</td><td>{text(process.status) || "STATUS UNAVAILABLE"}</td><td>{text(process.uptime) || "UPTIME UNAVAILABLE"}</td></tr>)}</tbody></table> : <DataUnavailable label="PROCESS / PID DATA UNAVAILABLE" kind="not-wired" />}</ReferencePanel>;
}

function SystemVitals({ status }: { status: SystemStatus | null }): ReactElement {
  return <ReferencePanel eyebrow="SYSTEM STATUS" title="" className="ref-system-vitals"><span className="ref-panel__subline">VITALS OVERVIEW</span><div className="ref-vitals-grid"><Metric label="CPU" value={status?.cpu ?? null} suffix="%" /><Metric label="RAM" value={status?.ram ?? null} suffix="%" /><Metric label="DISK" value={status?.disk ?? null} suffix="%" /><Metric label="NETWORK" value={status?.netKbps ?? null} suffix=" KB/S" /></div><dl className="ref-system-stats"><dt>UPTIME</dt><dd>{status?.uptimeSeconds === null || status?.uptimeSeconds === undefined ? "UNAVAILABLE" : `${status.uptimeSeconds}s`}</dd><dt>GPU</dt><dd>{status?.gpu === null || status?.gpu === undefined ? "UNAVAILABLE" : `${status.gpu}%`}</dd></dl></ReferencePanel>;
}

function ActivityFeed({ activities, content }: { activities: string[]; content: Record<string, unknown> }): ReactElement {
  const logs = Array.isArray(content.logs) ? content.logs.map(record) : [];
  if (!logs.length && !activities.length) return <ReferencePanel eyebrow="ACTIVITY FEED" title="" className="ref-activity-feed"><DataUnavailable label="NO SYSTEM ACTIVITY REPORTED" /></ReferencePanel>;
  return <ReferencePanel eyebrow="ACTIVITY FEED" title="" className="ref-activity-feed">{logs.length ? logs.slice(0, 8).map((log, index) => <p key={`${text(log.message)}-${index}`}><time>{text(log.timestamp) || "TIME UNAVAILABLE"}</time><b>{text(log.level) || "LEVEL UNAVAILABLE"}</b><span>{text(log.message) || "LOG MESSAGE UNAVAILABLE"}</span></p>) : activities.slice(0, 8).map((activity, index) => <p key={`${activity}-${index}`}><time>LIVE</time><b>ACTIVITY</b><span>{activity}</span></p>)}</ReferencePanel>;
}

function SystemTasksScreen({ data }: { data: ReferenceDataSnapshot }): ReactElement {
  return <div className="ref-screen ref-system" data-reference-screen="ref-system-tasks"><WorkspaceHeading eyebrow="SYSTEM / TASKS WORKSPACE" title="" subtitle="OVERVIEW" className="ref-system-heading" /><ReferencePanel eyebrow="NETWORK OVERVIEW" title="" className="ref-topology"><LiveMap data={spatialDataFor(data.systemContent, ["topology", "network_map"])} label="AUTHORITATIVE TOPOLOGY UNAVAILABLE" /></ReferencePanel><TaskStatus tasks={data.tasks} /><ProcessTable content={data.systemContent} /><SystemVitals status={data.systemStatus} /><ActivityFeed activities={data.activities} content={data.systemContent} /><ReferenceCore position="dock" /></div>;
}

function ActiveDockedScreen(): ReactElement {
  return <div className="ref-screen ref-active-docked" data-reference-screen="ref-active-docked"><WorkspaceHeading eyebrow="ACTIVE-WORK DOCKED-CORE" title="" subtitle="SYSTEM LINK STATUS NOT VERIFIED" /><ReferenceCore position="dock" /></div>;
}

function ApprovalScreen({ approval }: { approval: ToolApprovalRequest | null }): ReactElement {
  const title = approval ? text(approval.tool_name) || "PENDING ACTION" : "NO PENDING APPROVAL";
  return <div className="ref-screen ref-approval" data-reference-screen="ref-approval"><WorkspaceHeading eyebrow="OPERATOR / APPROVAL" title="" subtitle={approval ? "PENDING DECISION" : "REAL DATA / UNAVAILABLE STATE"} /><div className="ref-approval-ref">{approval ? `REQUEST ${approval.request_id}` : "NO ACTIVE REQUEST"}<br /><small>{approval ? "AWAITING OPERATOR INPUT" : "REAL DATA / UNAVAILABLE STATE"}</small></div><ReferencePanel eyebrow="ACTION REQUEST" title={title} className="ref-approval-request">{approval ? <><div className="ref-approval-subtitle">{text(approval.risk_class) || "RISK CLASS UNAVAILABLE"}</div><SectionRule label="SUMMARY" /><p className="ref-approval-summary">{text(approval.reason) || "APPROVAL REASON UNAVAILABLE"}</p></> : <DataUnavailable label="NO PENDING APPROVAL" />}<div className="ref-approval-columns"><div><SectionRule label="EXPECTED IMPACT" /><DataUnavailable label="IMPACT DETAILS UNAVAILABLE" kind="not-wired" /></div><div><SectionRule label="POTENTIAL RISKS" /><DataUnavailable label="RISK DETAILS UNAVAILABLE" kind="not-wired" /></div></div></ReferencePanel><ReferencePanel eyebrow="TARGET OVERVIEW" title="TARGET CONTEXT UNAVAILABLE" className="ref-approval-target"><DataUnavailable label="TARGET IMAGE / ROUTE CONTEXT UNAVAILABLE" kind="not-wired" /></ReferencePanel><section className="ref-operator-decision"><SectionRule label="OPERATOR DECISION" /><div><button type="button" className="ref-decision ref-decision--approve" disabled={!approval}><i>✓</i><span>{approval ? "APPROVE OPERATION" : "APPROVAL UNAVAILABLE"}<small>REAL DATA / UNAVAILABLE STATE</small></span></button><button type="button" className="ref-decision ref-decision--reject" disabled={!approval}><i>×</i><span>{approval ? "REJECT OPERATION" : "APPROVAL UNAVAILABLE"}<small>REAL DATA / UNAVAILABLE STATE</small></span></button></div><label>NOTE (OPTIONAL)<textarea placeholder="Operator notes unavailable in Visual Lab" disabled={!approval} /></label></section><ReferenceCore position="dock" /></div>;
}

function Toggle({ available }: { available: boolean }): ReactElement {
  return <span className={`ref-toggle ${available ? "is-on" : ""}`} aria-hidden="true"><i /></span>;
}

function SettingsRows({ fields }: { fields: ConfigField[] }): ReactElement {
  if (!fields.length) return <DataUnavailable label="RUNTIME CONFIGURATION UNAVAILABLE" />;
  return <>{fields.slice(0, 6).map((field) => <div className="ref-setting-row" key={field.key}><i>◈</i><span><strong>{field.label || field.key}</strong><small>{field.group || "CONFIGURATION"} · {field.type}</small></span><em>{field.secret ? (field.is_set ? "CONFIGURED" : "NOT CONFIGURED") : displayValue(field.value) || "UNAVAILABLE"}</em><Toggle available={Boolean(field.is_set || (!field.secret && displayValue(field.value) !== ""))} /></div>)}</>;
}

function SettingsScreen({ settings }: { settings: SettingsSnapshot }): ReactElement {
  const fields = settings.fields;
  return <div className="ref-screen ref-settings" data-reference-screen="ref-settings"><WorkspaceHeading eyebrow="SYSTEM / SETTINGS" title="" subtitle="CONFIGURE · MONITOR · CONTROL" /><nav className="ref-settings-nav" aria-label="Settings categories">{["CHARLIE", "SENSORS", "NETWORK", "SECURITY", "DISPLAY", "SYSTEM"].map((label, index) => <button type="button" className={index === 0 ? "is-selected" : ""} key={label}><i /><span>{label}<small>RUNTIME CATEGORY</small></span></button>)}</nav><ReferencePanel eyebrow="CHARLIE CONFIGURATION" title="" className="ref-settings-config"><span className="ref-panel__subline">MAIN-OWNED SETTINGS SNAPSHOT</span><SettingsRows fields={fields} /></ReferencePanel><ReferencePanel eyebrow="SAFETY & CONSTRAINTS" title="" className="ref-settings-safety"><span className="ref-panel__subline">OPERATIONAL BOUNDARIES</span><SettingsRows fields={fields.filter((field) => /privacy|safety|security|risk|oversight/i.test(`${field.group} ${field.label}`))} /></ReferencePanel><ReferencePanel eyebrow="SYSTEM PREFERENCES" title="" className="ref-settings-prefs"><span className="ref-panel__subline">INTERFACE & OPERATIONS</span><SettingsRows fields={fields.filter((field) => /appearance|display|system|hud|timezone|unit|shortcut/i.test(`${field.group} ${field.label}`))} /></ReferencePanel><ReferencePanel eyebrow="CHARLIE STATUS" title="" className="ref-settings-status"><span className="ref-panel__subline">MAIN HEALTH PROJECTION</span>{Object.keys(settings.health).length ? Object.entries(settings.health).slice(0, 6).map(([name, value]) => <p className="ref-module" key={name}><i />{name}<span>{text(value.status) || "UNAVAILABLE"}</span></p>) : <DataUnavailable label="RUNTIME HEALTH UNAVAILABLE" />}</ReferencePanel><ReferenceCore position="dock" /></div>;
}

function FaultScreen(): ReactElement {
  const runtime = useCharlieStore((state) => state.visualRuntime);
  return <div className="ref-screen ref-fault" data-reference-screen="ref-fault"><ReferenceCore position="center" tone="fault" /><div className="ref-fault-detail"><span>{runtime.detail || "SPECIFIC FAULT CONTEXT UNAVAILABLE"}</span><small>{runtime.phase === "error" ? "RUNTIME ERROR STATE OBSERVED" : "DATA CONTRACT MISSING / NOT WIRED"}</small></div><button type="button" className="ref-recovery-action" disabled={!runtime.recoveryProposalId}><i>↻</i><span>{runtime.recoveryProposalId ? "RECOVERY ACTION AVAILABLE" : "RECOVERY ACTION NOT WIRED"}<small>{runtime.recoveryProposalId ? "Authoritative recovery proposal present." : "No authoritative recovery proposal is available."}</small></span></button></div>;
}

function DegradedScreen({ health }: { health: Record<string, SubsystemHealth> }): ReactElement {
  const runtime = useCharlieStore((state) => state.visualRuntime);
  const rows = Object.entries(health);
  return <div className="ref-screen ref-degraded" data-reference-screen="ref-degraded"><WorkspaceHeading eyebrow="SYSTEM STATUS" title="" subtitle="PARTIAL CAPABILITY" /><ReferencePanel eyebrow="SUBSYSTEM HEALTH" title="" className="ref-degraded-health"><span className="ref-panel__subline">MAIN HEALTH PROJECTION</span>{rows.length ? rows.slice(0, 6).map(([name, value]) => <div className="ref-health-row" key={name}><i>◎</i><span>{name.toUpperCase()}</span><b><em style={{ width: value.status === "healthy" || value.status === "running" ? "100%" : "0%" }} /></b><strong>{text(value.status) || "UNAVAILABLE"}</strong><small>{text(value.detail) || "DETAIL UNAVAILABLE"}</small></div>) : <DataUnavailable label="SUBSYSTEM HEALTH UNAVAILABLE" />}</ReferencePanel><ReferencePanel eyebrow="CURRENT LIMITATIONS" title="" className="ref-degraded-limits"><DataUnavailable label="NO LIMITATION DETAILS REPORTED" kind="not-wired" /></ReferencePanel><ReferencePanel eyebrow="SYSTEM MESSAGE" title="" className="ref-degraded-message"><span className="ref-panel__subline">STATUS UPDATE</span><p>{runtime.detail || "REAL DATA / UNAVAILABLE STATE"}</p></ReferencePanel><ReferenceCore position="center" tone="amber" /></div>;
}

function ReferenceScreen({ scenario, data }: { scenario: ReferenceVisualScenario; data: ReferenceDataSnapshot }): ReactElement {
  switch (scenario) {
    case "ref-idle": return <div className="ref-screen ref-idle" data-reference-screen={scenario}><ReferenceCore position="center" /></div>;
    case "ref-online": return <OnlineScreen status={data.systemStatus} netHistory={data.netHistory} />;
    case "ref-research": return <ResearchScreen selected={false} screenId={scenario} payload={data.researchPayload} content={data.researchContent} result={data.latestResearchResult} />;
    case "ref-research-selected": return <ResearchScreen selected screenId={scenario} payload={data.researchPayload} content={data.researchContent} result={data.latestResearchResult} />;
    case "ref-vision": return <VisionScreen selected={false} screenId={scenario} content={data.visionContent} />;
    case "ref-vision-selected": return <VisionScreen selected screenId={scenario} content={data.visionContent} />;
    case "ref-briefing": return <BriefingScreen alt={false} screenId={scenario} payload={data.briefingPayload} content={data.briefingContent} />;
    case "ref-briefing-alt": return <BriefingScreen alt screenId={scenario} payload={data.briefingPayload} content={data.briefingContent} />;
    case "ref-system-tasks": return <SystemTasksScreen data={data} />;
    case "ref-active-docked": return <ActiveDockedScreen />;
    case "ref-approval": return <ApprovalScreen approval={data.approval} />;
    case "ref-settings": return <SettingsScreen settings={data.settings} />;
    case "ref-fault": return <FaultScreen />;
    case "ref-degraded": return <DegradedScreen health={data.subsystemHealth} />;
  }
}

function isCenteredScenario(scenario: ReferenceVisualScenario): boolean {
  return ["ref-idle", "ref-online", "ref-fault", "ref-degraded"].includes(scenario);
}

export function ReferenceVisualLab({ scenario }: ReferenceVisualLabProps): ReactElement {
  const connected = useCharlieStore((state) => state.connected);
  const intents = useCharlieStore((state) => state.presentationIntents);
  const latestResearchResult = useCharlieStore((state) => state.latestResearchResult);
  const systemStatus = useCharlieStore((state) => state.systemStatus);
  const subsystemHealth = useCharlieStore((state) => state.subsystemHealth);
  const tasks = useCharlieStore((state) => state.tasks);
  const activities = useCharlieStore((state) => state.activities);
  const approval = useCharlieStore((state) => state.activeToolApproval);
  const netHistory = useCharlieStore((state) => state.netHistory);
  const settings = useSettingsSnapshot(scenario === "ref-settings");
  const research = useMemo(() => researchPayloadFor(workspaceIntent(intents, "research")), [intents]);
  const briefing = useMemo(() => briefingPayloadFor(workspaceIntent(intents, "briefing")), [intents]);
  const visionContent = workspaceIntent(intents, "vision")?.content || {};
  const systemContent = workspaceIntent(intents, "system")?.content || {};
  const centered = isCenteredScenario(scenario);
  const sourceLabel = connected ? "SANDBOXED RUNTIME" : "NOT VERIFIED";
  const data: ReferenceDataSnapshot = { connected, intents, researchPayload: research.payload, researchContent: research.content, latestResearchResult, briefingPayload: briefing.payload, briefingContent: briefing.content, visionContent, systemContent, systemStatus, subsystemHealth, tasks, activities, approval, netHistory, settings };
  return <main className={`charlie-scene-root reference-lab reference-lab--${centered ? "centered" : "workspace"}`} data-visual-lab="TEST/MOCK" data-reference-lab="TEST/MOCK" data-reference-scenario={scenario} data-reference-source={sourceLabel} data-core-position={centered ? "center" : "dock_bottom_right"} aria-label={`TEST/MOCK reference visual scenario ${scenario}`}><EnvironmentLayer corePosition={centered ? "center" : "dock_bottom_right"} hasWorkspace={!centered} /><div className="reference-lab__content"><ReferenceScreen scenario={scenario} data={data} /></div><div className="reference-proof-badge">TEST/MOCK · {sourceLabel}</div><div className="sr-only">TEST/MOCK reference visual lab — not runtime acceptance</div></main>;
}
