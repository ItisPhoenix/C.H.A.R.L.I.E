export interface PresentationSource {
  id: string;
  title: string;
  domain: string;
  url: string;
  snippet: string;
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

export interface BriefingStory {
  id: string;
  title: string;
  summary: string;
  source_ids: string[];
  published_at?: string;
  region?: string;
}

export interface TimelinePayloadItem {
  id?: string;
  kind?: "published" | string;
  timestamp?: string;
  time?: string;
  title: string;
  summary?: string;
  status?: string;
}

export interface ResearchWorkspacePayload {
  schema: "charlie.research_workspace";
  version: 1;
  query: string;
  mode: string;
  title: string;
  summary: string;
  status: string;
  confidence: number;
  findings: ResearchFinding[];
  sources: PresentationSource[];
  timeline_items: TimelinePayloadItem[];
  [key: string]: unknown;
}

export interface BriefingWorkspacePayload {
  schema: "charlie.briefing_workspace";
  version: 1;
  title: string;
  headline: string;
  summary: string;
  stories: BriefingStory[];
  summaries: string[];
  timeline_items: TimelinePayloadItem[];
  sources: PresentationSource[];
  status: string;
  confidence: number;
  [key: string]: unknown;
}

const RESEARCH_SCHEMA = "charlie.research_workspace" as const;
const RESEARCH_VERSION = 1 as const;
const BRIEFING_SCHEMA = "charlie.briefing_workspace" as const;
const BRIEFING_VERSION = 1 as const;

function hasOwn(content: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(content, key);
}

function isLegacyPayload(content: Record<string, unknown>): boolean {
  return !hasOwn(content, "schema") && !hasOwn(content, "version");
}

function isCanonicalPayload(
  content: Record<string, unknown>,
  schema: string,
  version: number,
  required: string[],
): boolean {
  return content.schema === schema && content.version === version && required.every((key) => hasOwn(content, key));
}

function unsupportedResearchPayload(): ResearchWorkspacePayload {
  return {
    schema: RESEARCH_SCHEMA,
    version: RESEARCH_VERSION,
    query: "",
    mode: "standard",
    title: "RESEARCH & SYNTHESIS",
    summary: "Unsupported research workspace payload.",
    status: "unsupported",
    confidence: 0,
    findings: [],
    sources: [],
    timeline_items: [],
  };
}

function unsupportedBriefingPayload(): BriefingWorkspacePayload {
  return {
    schema: BRIEFING_SCHEMA,
    version: BRIEFING_VERSION,
    title: "Daily Briefing",
    headline: "Daily Intelligence Briefing",
    summary: "Unsupported briefing workspace payload.",
    stories: [],
    summaries: [],
    timeline_items: [],
    sources: [],
    status: "unsupported",
    confidence: 0,
  };
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function sourceItems(value: unknown): PresentationSource[] {
  if (!Array.isArray(value)) return [];
  return value.map((item, index) => {
    const source = record(item);
    return {
      id: text(source.id ?? source.source_id, `S${index + 1}`),
      title: text(source.title, "SOURCE EVIDENCE"),
      domain: text(source.domain ?? source.publisher),
      url: text(source.url),
      snippet: text(source.snippet ?? source.excerpt),
      published_at: typeof source.published_at === "string" ? source.published_at : null,
      source_type: typeof source.source_type === "string" ? source.source_type : typeof source.type === "string" ? source.type : null,
      confidence: typeof source.confidence === "number" ? source.confidence : null,
    };
  });
}

function finiteConfidence(value: unknown): boolean {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

function validCanonicalSources(value: unknown): value is Array<Record<string, unknown>> {
  return Array.isArray(value) && value.every((item) => {
    const source = record(item);
    return typeof source.id === "string"
      && Boolean(source.id.trim())
      && typeof source.title === "string"
      && Boolean(source.title.trim())
      && (source.url === undefined || source.url === null || typeof source.url === "string")
      && (source.snippet === undefined || source.snippet === null || typeof source.snippet === "string")
      && (source.domain === undefined || source.domain === null || typeof source.domain === "string")
      && (source.published_at === undefined || source.published_at === null || typeof source.published_at === "string")
      && (source.confidence === undefined || source.confidence === null || finiteConfidence(source.confidence));
  });
}

function validCanonicalTimeline(value: unknown): boolean {
  return value === undefined || (
    Array.isArray(value)
    && value.every((item) => {
      const timeline = record(item);
      return typeof timeline.id === "string"
        && Boolean(timeline.id.trim())
        && typeof timeline.title === "string"
        && Boolean(timeline.title.trim())
        && (timeline.timestamp === undefined || timeline.timestamp === null || typeof timeline.timestamp === "string")
        && (timeline.time === undefined || timeline.time === null || typeof timeline.time === "string");
    })
  );
}

function validCanonicalResearchRecords(content: Record<string, unknown>): boolean {
  if (!validCanonicalSources(content.sources)) return false;
  const sourceIds = new Set(content.sources.map((source) => source.id));
  if (!Array.isArray(content.findings) || !content.findings.every((item) => {
    const finding = record(item);
    return typeof finding.id === "string"
      && Boolean(finding.id.trim())
      && typeof finding.title === "string"
      && Boolean(finding.title.trim())
      && typeof finding.detail === "string"
      && Array.isArray(finding.source_ids)
      && finding.source_ids.every((id) => typeof id === "string" && sourceIds.has(id))
      && (finding.confidence === undefined || finiteConfidence(finding.confidence))
      && (finding.contradiction === undefined || typeof finding.contradiction === "boolean");
  })) return false;
  return validCanonicalTimeline(content.timeline_items);
}

function validCanonicalBriefingRecords(content: Record<string, unknown>): boolean {
  if (!validCanonicalSources(content.sources)) return false;
  const sourceIds = new Set(content.sources.map((source) => source.id));
  if (!Array.isArray(content.summaries) || content.summaries.some((summary) => typeof summary !== "string")) return false;
  if (!Array.isArray(content.stories) || !content.stories.every((item) => {
    const story = record(item);
    return typeof story.id === "string"
      && Boolean(story.id.trim())
      && typeof story.title === "string"
      && Boolean(story.title.trim())
      && typeof story.summary === "string"
      && Array.isArray(story.source_ids)
      && story.source_ids.every((id) => typeof id === "string" && sourceIds.has(id))
      && (story.published_at === undefined || story.published_at === null || typeof story.published_at === "string")
      && (story.region === undefined || story.region === null || typeof story.region === "string");
  })) return false;
  return validCanonicalTimeline(content.timeline_items);
}

function validSourceIds(ids: unknown, sources: PresentationSource[]): string[] {
  const known = new Set(sources.map((source) => source.id));
  return Array.isArray(ids)
    ? ids.filter((id): id is string => typeof id === "string" && known.has(id))
    : [];
}

function timelineItems(value: unknown): TimelinePayloadItem[] {
  if (!Array.isArray(value)) return [];
  return value.map((item, index) => {
    const timeline = record(item);
    return {
      id: text(timeline.id, `timeline-${index + 1}`),
      kind: text(timeline.kind, "published"),
      timestamp: text(timeline.timestamp ?? timeline.published_at),
      time: text(timeline.time ?? timeline.timestamp, ""),
      title: text(timeline.title ?? timeline.event ?? timeline.description),
      summary: text(timeline.summary),
      status: text(timeline.status),
    };
  });
}

export function normalizeResearchWorkspacePayload(input: unknown): ResearchWorkspacePayload {
  const content = record(input);
  const canonical = isCanonicalPayload(content, RESEARCH_SCHEMA, RESEARCH_VERSION, [
      "query", "mode", "summary", "status", "confidence", "findings", "sources",
    ]);
  if (!isLegacyPayload(content) && !canonical) {
    return unsupportedResearchPayload();
  }
  if (canonical && !validCanonicalResearchRecords(content)) {
    return unsupportedResearchPayload();
  }
  const sources = sourceItems(content.sources ?? content.evidence);
  const rawFindings = Array.isArray(content.findings)
    ? content.findings
    : Array.isArray(content.keyFindings) ? content.keyFindings : Array.isArray(content.insights) ? content.insights : [];
  const findings: ResearchFinding[] = rawFindings.map((item, index) => {
    const finding = record(item);
    return {
      id: text(finding.id, `F${index + 1}`),
      title: text(finding.title ?? finding.label, `Finding ${String(index + 1).padStart(2, "0")}`),
      detail: text(finding.detail ?? finding.text ?? finding.summary),
      source_ids: validSourceIds(finding.source_ids, sources),
      confidence: typeof finding.confidence === "number" ? finding.confidence : undefined,
      contradiction: typeof finding.contradiction === "boolean" ? finding.contradiction : undefined,
    };
  });
  return {
    ...content,
    schema: RESEARCH_SCHEMA,
    version: RESEARCH_VERSION,
    query: text(content.query ?? content.subtitle),
    mode: text(content.mode, "standard"),
    title: text(content.title, "RESEARCH & SYNTHESIS"),
    summary: text(content.summary ?? content.objective, "No grounded findings were returned."),
    status: text(content.status, "partial"),
    confidence: typeof content.confidence === "number" ? content.confidence : 0,
    findings,
    sources,
    timeline_items: timelineItems(content.timeline_items ?? content.timeline),
  };
}

export function normalizeBriefingWorkspacePayload(input: unknown): BriefingWorkspacePayload {
  const content = record(input);
  const canonical = isCanonicalPayload(content, BRIEFING_SCHEMA, BRIEFING_VERSION, [
      "headline", "summary", "stories", "summaries", "sources", "status", "confidence",
    ]);
  if (!isLegacyPayload(content) && !canonical) {
    return unsupportedBriefingPayload();
  }
  if (canonical && !validCanonicalBriefingRecords(content)) {
    return unsupportedBriefingPayload();
  }
  const sources = sourceItems(content.sources ?? content.evidence);
  const rawStories = Array.isArray(content.stories) ? content.stories : [];
  const stories: BriefingStory[] = rawStories.map((item, index) => {
    const story = record(item);
    return {
      id: text(story.id, `ST${index + 1}`),
      title: text(story.title, `Story ${index + 1}`),
      summary: text(story.summary),
      source_ids: validSourceIds(story.source_ids, sources),
      published_at: typeof story.published_at === "string" ? story.published_at : undefined,
      region: typeof story.region === "string" ? story.region : undefined,
    };
  });
  const summaries = Array.isArray(content.summaries)
    ? content.summaries.filter((item): item is string => typeof item === "string")
    : stories.map((story) => story.summary).filter(Boolean);
  return {
    ...content,
    schema: BRIEFING_SCHEMA,
    version: BRIEFING_VERSION,
    title: text(content.title, "Daily Briefing"),
    headline: text(content.headline ?? content.title, "Daily Intelligence Briefing"),
    summary: text(content.summary, "No grounded briefing stories were returned."),
    stories,
    summaries,
    timeline_items: timelineItems(content.timeline_items ?? content.timeline),
    sources,
    status: text(content.status, "partial"),
    confidence: typeof content.confidence === "number" ? content.confidence : 0,
  };
}
