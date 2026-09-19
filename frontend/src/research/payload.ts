import contract from "../../../shared/workspace_payload_contract.json";

export type EvidenceClass = "REAL RUNTIME" | "SANDBOXED RUNTIME" | "TEST/MOCK" | "FIXTURE" | "NOT VERIFIED";

export interface ResearchSource {
  id: string;
  title: string;
  domain?: string;
  url?: string;
  snippet?: string;
  published_at?: string | null;
  source_type?: string | null;
  confidence?: number | null;
}

export interface ResearchFinding {
  id: string;
  title: string;
  detail: string;
  source_ids: string[];
  confidence?: number;
  contradiction?: boolean;
}

export interface ResearchTimelineItem {
  id: string;
  title: string;
  summary?: string;
  timestamp?: string;
  time?: string;
  status?: string;
}

export interface ResearchPayload {
  schema: "charlie.research_workspace";
  version: 1;
  query: string;
  objective?: string;
  mode: string;
  title?: string;
  summary: string;
  status: string;
  confidence: number;
  findings: ResearchFinding[];
  sources: ResearchSource[];
  timeline_items?: ResearchTimelineItem[];
  stop_reason?: string | null;
  plan?: unknown;
  [key: string]: unknown;
}

const spec = contract.payloads.research;

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function confidence(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
}

function sourceRecords(value: unknown): ResearchSource[] | null {
  if (!Array.isArray(value)) return null;
  const sources: ResearchSource[] = [];
  for (const raw of value) {
    const source = record(raw);
    const id = text(source?.id);
    const title = text(source?.title);
    if (!source || !id || !title) return null;
    const item: ResearchSource = { id, title };
    for (const key of ["domain", "url", "snippet", "published_at", "source_type"] as const) {
      const value = source[key];
      if (value !== undefined && value !== null && typeof value !== "string") return null;
      if (typeof value === "string") item[key] = value;
      if (value === null && (key === "published_at" || key === "source_type")) item[key] = null;
    }
    if (source.confidence !== undefined && source.confidence !== null) {
      const value = confidence(source.confidence);
      if (value === null) return null;
      item.confidence = value;
    }
    sources.push(item);
  }
  return sources;
}

function findingRecords(value: unknown, sourceIds: Set<string>): ResearchFinding[] | null {
  if (!Array.isArray(value)) return null;
  const findings: ResearchFinding[] = [];
  for (const raw of value) {
    const finding = record(raw);
    const id = text(finding?.id);
    const title = text(finding?.title);
    const detail = text(finding?.detail);
    if (!finding || !id || !title || !detail || !Array.isArray(finding.source_ids)) return null;
    const source_ids = finding.source_ids.filter((sourceId): sourceId is string =>
      typeof sourceId === "string" && sourceIds.has(sourceId));
    if (source_ids.length !== finding.source_ids.length) return null;
    const item: ResearchFinding = { id, title, detail, source_ids };
    if (finding.confidence !== undefined) {
      const value = confidence(finding.confidence);
      if (value === null) return null;
      item.confidence = value;
    }
    if (finding.contradiction !== undefined) {
      if (typeof finding.contradiction !== "boolean") return null;
      item.contradiction = finding.contradiction;
    }
    findings.push(item);
  }
  return findings;
}

function timelineRecords(value: unknown): ResearchTimelineItem[] | undefined | null {
  if (value === undefined) return undefined;
  if (!Array.isArray(value)) return null;
  const timeline: ResearchTimelineItem[] = [];
  for (const raw of value) {
    const item = record(raw);
    const id = text(item?.id);
    const title = text(item?.title);
    if (!item || !id || !title) return null;
    const result: ResearchTimelineItem = { id, title };
    for (const key of ["summary", "timestamp", "time", "status"] as const) {
      if (item[key] !== undefined && item[key] !== null && typeof item[key] !== "string") return null;
      if (typeof item[key] === "string") result[key] = item[key] as string;
    }
    timeline.push(result);
  }
  return timeline;
}

export function normalizeResearchPayload(input: unknown): ResearchPayload | null {
  const content = record(input);
  if (!content || content.schema !== spec.schema || content.version !== spec.version) return null;
  if (spec.required.some((field) => !(field in content))) return null;

  const query = text(content.query);
  const mode = text(content.mode);
  const summary = typeof content.summary === "string" ? content.summary : null;
  const status = text(content.status);
  const confidenceValue = confidence(content.confidence);
  if (!query || !mode || summary === null || !status || confidenceValue === null) return null;

  const sources = sourceRecords(content.sources);
  if (!sources) return null;
  const findings = findingRecords(content.findings, new Set(sources.map((source) => source.id)));
  if (!findings) return null;
  const timeline_items = timelineRecords(content.timeline_items);
  if (timeline_items === null) return null;

  const payload: ResearchPayload = {
    ...content,
    schema: "charlie.research_workspace",
    version: 1,
    query,
    mode,
    summary,
    status,
    confidence: confidenceValue,
    findings,
    sources,
  };
  if (typeof content.objective === "string") payload.objective = content.objective;
  if (typeof content.title === "string") payload.title = content.title;
  if (typeof content.stop_reason === "string" || content.stop_reason === null) payload.stop_reason = content.stop_reason;
  if (timeline_items) payload.timeline_items = timeline_items;
  return payload;
}

export function researchTruthForPayload(payload: ResearchPayload): EvidenceClass {
  if (["error", "timeout", "no_results", "cancelled"].includes(payload.status.toLowerCase())) {
    return "SANDBOXED RUNTIME";
  }
  return payload.sources.length > 0 ? "REAL RUNTIME" : "NOT VERIFIED";
}
