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
    schema: "charlie.research_workspace",
    version: 1,
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
    schema: "charlie.briefing_workspace",
    version: 1,
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
