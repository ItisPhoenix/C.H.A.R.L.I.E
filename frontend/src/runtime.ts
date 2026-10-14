import type { OrbState } from "thinking-orbs";

export type SceneSnapshot = {
  revision: number;
  title: string;
  sessionId?: string;
  summary?: string;
  details: Array<{ label: string; value: string }>;
  conversationHistory?: Array<{ id: string; role: "you" | "charlie"; text: string; turnId?: string }>;
  activity?: RuntimeEvent[];
  widgets?: DashboardWidgets;
  tasks?: RuntimeTask[];
  conversationState?: "working" | "ready";
  activeTurnId?: string | null;
  activeTaskId?: string | null;
  voice?: { enabled: boolean; mic_muted: boolean; muted: boolean; volume: number };
  researchResult?: ResearchResultData;
  researchResults?: ResearchResultData[];
  settings?: RuntimeSetting[];
  pendingApproval?: RuntimeEvent;
};

export type ResearchSource = {
  id: string;
  title: string;
  url: string;
  class?: string;
};

export type ResearchCoverage = {
  id: string;
  question: string;
  requiredFields: string[];
  supportingClaimIds: string[];
  refutingClaimIds: string[];
  status: "supported" | "contradicted" | "unresolved";
  conflict: boolean;
  missingEvidence: string[];
};

export type ResearchPassage = {
  id: string;
  sourceId: string;
  sourceUrl: string;
  canonicalUrl: string;
  documentHash: string;
  text: string;
  startOffset: number;
  endOffset: number;
  retrievedAt: string;
  publishedAt: string | null;
  sourceClass: string;
};

export type ResearchClaim = {
  id: string;
  text: string;
  subject: string;
  predicate: string;
  value: string;
  units: string;
  timeScope: string;
  evidencePassageIds: string[];
  relationship: "supports" | "refutes";
  verificationStatus: string;
};

export type ResearchFact = {
  candidate: string | null;
  aspect: string;
  value: string;
  quote: string;
  sourceId: string;
  url: string;
};

export type ResearchResource = {
  title: string;
  url: string;
  domain: string;
};

export type ResearchProviderOutcome = {
  status: "ok" | "empty" | "failed" | "unavailable" | "challenge";
  resultCount: number;
};

export type ResearchResultData = {
  id: string;
  text: string;
  query: string;
  turnId?: string;
  channel?: "voice" | "telegram" | "web" | "console";
  mode?: "quick" | "standard" | "deep";
  answer?: string;
  partial?: boolean;
  synthesis?: string;
  gaps?: string[];
  sources?: ResearchSource[];
  searchResults?: ResearchResource[];
  readPages?: ResearchResource[];
  sourceUrls?: string[];
  searchResultCount?: number;
  documentCount?: number;
  evidenceCount?: number;
  passageCount?: number;
  factCount?: number;
  durationMs?: number;
  providerOutcomes?: ResearchProviderOutcome[];
  stopReason?: string;
  completeness?: "complete" | "partial" | "none";
  terminationReason?: "completed" | "deadline" | "cancelled" | "provider_exhausted" | "error";
  coverage?: ResearchCoverage[];
  passages?: ResearchPassage[];
  claims?: ResearchClaim[];
  verifiedFacts?: ResearchFact[];
  completedAt?: string;
};

export type RuntimeSetting = {
  key: string;
  group: string;
  label: string;
  type: "bool" | "int" | "float" | "list" | "str";
  secret: boolean;
  value: string | number | boolean | string[] | null;
  isSet: boolean;
  pending: boolean;
};

export type RuntimeTask = {
  id: string;
  title: string;
  status: string;
  currentAction?: string;
  errorSummary?: string;
  updatedAt?: string;
};

export type DashboardWidgets = {
  clockVisible: boolean;
  hiddenWidgets?: string[];
  timers: Array<{ id: string; text: string; dueAt: string; timezone?: string; deliveryChannel?: "web" | "voice" }>;
  systemStatus?: DashboardSystemStatus;
  weather?: {
    location: string;
    temperature: number;
    temperatureUnit: string;
    feelsLike?: number;
    humidity?: number;
    windSpeed?: number;
    windSpeedUnit?: string;
    weatherCode?: number;
    observedAt?: string;
    sunset?: string;
  };
  stopwatch?: { state: "running" | "stopped"; elapsedSeconds: number; sampledAt: string };
  media?: { available: boolean; adapterAvailable: boolean; title: string; artist: string; app?: string; artUri?: string; status: string; positionSeconds: number; durationSeconds: number };
};

export type DashboardSystemStatus = {
  cpuPercent: number;
  memoryPercent: number;
  gpuPercent?: number;
  diskPercent?: number;
  networkKBPerSecond?: number;
  uptimeSeconds?: number;
  batteryPercent?: number;
  sampledAtMs?: number;
};

export function orbStateForEvent(event: string | Pick<RuntimeEvent, "type" | "stage">): OrbState | null {
  const { type, stage } = typeof event === "string" ? { type: event, stage: undefined } : event;
  if (["transcript", "vad_start", "ptt_start"].includes(type)) return "listening";
  if (type === "research_progress") {
    if (stage === "planning") return "weaving";
    if (stage === "found") return "shaping";
    if (stage === "synthesizing") return "composing";
    if (["searching", "reading"].includes(stage ?? "")) return "searching";
    if (["iterating", "done"].includes(stage ?? "")) return "solving";
    return "searching";
  }
  if (["thinking", "thinking_update"].includes(type)) return stage === "planning" ? "weaving" : "shaping";
  if (["tool_call", "background_task"].includes(type)) return "working";
  if (["token", "speaking_start"].includes(type)) return "composing";
  if (["tool_approval_request", "alert"].includes(type)) return "solving";
  if (["response_done", "speaking_stop"].includes(type)) return "breathing";
  return null;
}

export type RuntimeEvent = {
  sourceCount?: number;
  evidenceCount?: number;
  findingCount?: number;
  id?: string;
  type: string;
  source?: string;
  timestamp?: string;
  snapshot?: SceneSnapshot;
  text?: string;
  partial?: boolean;
  level?: number;
  summary?: string;
  stage?: string;
  query?: string;
  taskId?: string;
  turnId?: string;
  sessionId?: string;
  resultId?: string;
  requestId?: string;
  channel?: "voice" | "telegram" | "web" | "console";
  operationPreview?: string;
  researchResult?: ResearchResultData;
  widgets?: DashboardWidgets;
  systemStatus?: DashboardSystemStatus;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function stringList(value: unknown, limit = 240): string[] | undefined {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string").slice(0, limit)
    : undefined;
}

function parseSystemStatus(value: unknown): DashboardSystemStatus | undefined {
  if (!isRecord(value)
    || !finiteNumber(value.cpu) || value.cpu < 0 || value.cpu > 100
    || !finiteNumber(value.ram) || value.ram < 0 || value.ram > 100) return undefined;
  return {
    cpuPercent: value.cpu,
    memoryPercent: value.ram,
    ...(finiteNumber(value.gpu) && value.gpu >= 0 && value.gpu <= 100 ? { gpuPercent: value.gpu } : {}),
    ...(finiteNumber(value.disk_percent) && value.disk_percent >= 0 && value.disk_percent <= 100
      ? { diskPercent: value.disk_percent } : {}),
    ...(finiteNumber(value.net_kbps) && value.net_kbps >= 0 ? { networkKBPerSecond: value.net_kbps } : {}),
    ...(finiteNumber(value.uptime_seconds) && value.uptime_seconds >= 0 ? { uptimeSeconds: value.uptime_seconds } : {}),
    ...(finiteNumber(value.battery_percent) && value.battery_percent >= 0 && value.battery_percent <= 100
      ? { batteryPercent: value.battery_percent } : {}),
    ...(Number.isSafeInteger(value.sampled_at_ms) && Number(value.sampled_at_ms) >= 0
      ? { sampledAtMs: Number(value.sampled_at_ms) } : {}),
  };
}

function webUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    return ["http:", "https:"].includes(new URL(value).protocol);
  } catch {
    return false;
  }
}

function parseResearchResult(value: unknown): ResearchResultData | undefined {
  if (!isRecord(value)) return undefined;
  const id = typeof value.result_id === "string" ? value.result_id : value.id;
  if (typeof id !== "string" || typeof value.text !== "string" || typeof value.query !== "string") return undefined;
  const sources: ResearchSource[] = Array.isArray(value.sources)
    ? value.sources.flatMap((item) => {
      if (!isRecord(item) || typeof item.id !== "string" || typeof item.title !== "string" || !webUrl(item.url)) return [];
      return [{ id: item.id, title: item.title, url: item.url, ...(typeof item.class === "string" ? { class: item.class } : {}) }];
    }).slice(0, 80)
    : [];
  const sourceUrls = Array.isArray(value.source_urls)
    ? value.source_urls.filter(webUrl).slice(0, 80)
    : undefined;
  const resources = (key: "search_results" | "read_pages"): ResearchResource[] => Array.isArray(value[key])
    ? value[key].flatMap((item) => {
      if (!isRecord(item)) return [];
      const url = webUrl(item.url) ? item.url : item.canonical_url;
      if (!webUrl(url)) return [];
      const domain = typeof item.domain === "string" && item.domain ? item.domain : new URL(url).hostname;
      return [{
        title: typeof item.title === "string" && item.title.trim() ? item.title : domain,
        url,
        domain,
      }];
    }).slice(0, 80)
    : [];
  const searchResults = resources("search_results");
  const fetchedPages = resources("read_pages");
  const readPages = fetchedPages.length ? fetchedPages : (sourceUrls ?? []).map((url) => {
    const domain = new URL(url).hostname;
    return { title: sources.find((source) => source.url === url)?.title || domain, url, domain };
  });
  const coverage: ResearchCoverage[] = Array.isArray(value.coverage)
    ? value.coverage.flatMap((item) => {
      if (!isRecord(item) || typeof item.id !== "string" || typeof item.question !== "string") return [];
      if (!["supported", "contradicted", "unresolved"].includes(String(item.status))) return [];
      return [{
        id: item.id,
        question: item.question,
        requiredFields: stringList(item.required_fields, 32) ?? [],
        supportingClaimIds: stringList(item.supporting_claim_ids, 240) ?? [],
        refutingClaimIds: stringList(item.refuting_claim_ids, 240) ?? [],
        status: item.status as ResearchCoverage["status"],
        conflict: item.conflict === true,
        missingEvidence: stringList(item.missing_evidence, 32) ?? [],
      }];
    }).slice(0, 80)
    : [];
  const passages: ResearchPassage[] = Array.isArray(value.passages)
    ? value.passages.flatMap((item) => {
      if (!isRecord(item)) return [];
      if (typeof item.id !== "string" || typeof item.source_id !== "string" || !webUrl(item.source_url) || !webUrl(item.canonical_url)) return [];
      if (typeof item.document_hash !== "string" || typeof item.text !== "string" || typeof item.retrieved_at !== "string") return [];
      if (typeof item.start_offset !== "number" || !Number.isSafeInteger(item.start_offset) || typeof item.end_offset !== "number" || !Number.isSafeInteger(item.end_offset) || item.start_offset < 0 || item.end_offset < item.start_offset) return [];
      return [{
        id: item.id,
        sourceId: item.source_id,
        sourceUrl: item.source_url,
        canonicalUrl: item.canonical_url,
        documentHash: item.document_hash,
        text: item.text,
        startOffset: item.start_offset,
        endOffset: item.end_offset,
        retrievedAt: item.retrieved_at,
        publishedAt: typeof item.published_at === "string" ? item.published_at : null,
        sourceClass: typeof item.source_class === "string" ? item.source_class : "unknown",
      }];
    }).slice(0, 240)
    : [];
  const claims: ResearchClaim[] = Array.isArray(value.claims)
    ? value.claims.flatMap((item) => {
      if (!isRecord(item) || typeof item.id !== "string" || typeof item.text !== "string") return [];
      if (item.relationship !== "supports" && item.relationship !== "refutes") return [];
      const evidencePassageIds = stringList(item.evidence_passage_ids, 240) ?? [];
      if (!evidencePassageIds.length) return [];
      return [{
        id: item.id,
        text: item.text,
        subject: typeof item.subject === "string" ? item.subject : "",
        predicate: typeof item.predicate === "string" ? item.predicate : "",
        value: typeof item.value === "string" ? item.value : "",
        units: typeof item.units === "string" ? item.units : "",
        timeScope: typeof item.time_scope === "string" ? item.time_scope : "",
        evidencePassageIds,
        relationship: item.relationship as ResearchClaim["relationship"],
        verificationStatus: typeof item.verification_status === "string" ? item.verification_status : "unverified",
      }];
    }).slice(0, 240)
    : [];
  const verifiedFacts: ResearchFact[] = Array.isArray(value.verified_facts)
    ? value.verified_facts.flatMap((item) => {
      if (!isRecord(item) || typeof item.aspect !== "string" || typeof item.value !== "string" || typeof item.quote !== "string" || typeof item.source_id !== "string" || !webUrl(item.url)) return [];
      return [{ candidate: typeof item.candidate === "string" ? item.candidate : null, aspect: item.aspect, value: item.value, quote: item.quote, sourceId: item.source_id, url: item.url }];
    }).slice(0, 240)
    : [];
  const providerOutcomes: ResearchProviderOutcome[] = Array.isArray(value.provider_outcomes)
    ? value.provider_outcomes.flatMap((item) => {
      if (!isRecord(item)) return [];
      if (!(["ok", "empty", "failed", "unavailable", "challenge"] as const).includes(item.status as ResearchProviderOutcome["status"])) return [];
      if (typeof item.result_count !== "number" || !Number.isSafeInteger(item.result_count) || item.result_count < 0) return [];
      const status = item.status as ResearchProviderOutcome["status"];
      const countMatchesStatus = status === "ok" ? item.result_count > 0 : item.result_count === 0;
      if (!countMatchesStatus) return [];
      return [{ status, resultCount: item.result_count }];
    }).slice(0, 240)
    : [];
  const count = (key: string) => typeof value[key] === "number" && Number.isSafeInteger(value[key]) && value[key] >= 0 ? value[key] as number : undefined;
  const durationMs = typeof value.duration_ms === "number" && Number.isFinite(value.duration_ms) && value.duration_ms >= 0 ? value.duration_ms : undefined;
  const completeness = ["complete", "partial", "none"].includes(String(value.completeness)) ? value.completeness as ResearchResultData["completeness"] : undefined;
  const terminationReason = ["completed", "deadline", "cancelled", "provider_exhausted", "error"].includes(String(value.termination_reason)) ? value.termination_reason as ResearchResultData["terminationReason"] : undefined;
  const mode = ["quick", "standard", "deep"].includes(String(value.mode)) ? value.mode as ResearchResultData["mode"] : undefined;
  return {
    id,
    text: value.text,
    query: value.query,
    ...(typeof value.turn_id === "string" ? { turnId: value.turn_id } : {}),
    ...(["voice", "telegram", "web", "console"].includes(String(value.channel)) ? { channel: value.channel as ResearchResultData["channel"] } : {}),
    ...(mode ? { mode } : {}),
    ...(typeof value.answer === "string" ? { answer: value.answer } : {}),
    ...(typeof value.partial === "boolean" ? { partial: value.partial } : {}),
    ...(typeof value.synthesis === "string" ? { synthesis: value.synthesis } : {}),
    ...(stringList(value.gaps, 80) ? { gaps: stringList(value.gaps, 80) } : {}),
    ...(Array.isArray(value.sources) ? { sources } : {}),
    ...(Array.isArray(value.search_results) ? { searchResults } : {}),
    ...(Array.isArray(value.read_pages) || sourceUrls?.length ? { readPages } : {}),
    ...(sourceUrls ? { sourceUrls } : {}),
    ...(count("search_result_count") !== undefined ? { searchResultCount: count("search_result_count") } : {}),
    ...(count("document_count") !== undefined ? { documentCount: count("document_count") } : {}),
    ...(count("evidence_count") !== undefined ? { evidenceCount: count("evidence_count") } : {}),
    ...(count("passage_count") !== undefined ? { passageCount: count("passage_count") } : {}),
    ...(count("fact_count") !== undefined ? { factCount: count("fact_count") } : {}),
    ...(durationMs !== undefined ? { durationMs } : {}),
    ...(providerOutcomes.length ? { providerOutcomes } : {}),
    ...(typeof value.stop_reason === "string" ? { stopReason: value.stop_reason } : {}),
    ...(completeness ? { completeness } : {}),
    ...(terminationReason ? { terminationReason } : {}),
    ...(coverage.length ? { coverage } : {}),
    ...(passages.length ? { passages } : {}),
    ...(claims.length ? { claims } : {}),
    ...(verifiedFacts.length ? { verifiedFacts } : {}),
    ...(typeof value.completed_at === "string" ? { completedAt: value.completed_at } : {}),
  };
}

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function parseDashboardWidgets(value: unknown): DashboardWidgets | undefined {
  if (!isRecord(value)) return undefined;
  const timers = Array.isArray(value.timers)
    ? value.timers.flatMap((item) => {
      if (!isRecord(item) || typeof item.id !== "string" || typeof item.text !== "string" || typeof item.due_at !== "string" || !Number.isFinite(Date.parse(item.due_at))) return [];
      return [{
        id: item.id,
        text: item.text,
        dueAt: item.due_at,
        ...(typeof item.timezone === "string" ? { timezone: item.timezone } : {}),
        ...(item.delivery_channel === "web" || item.delivery_channel === "voice"
          ? { deliveryChannel: item.delivery_channel as "web" | "voice" }
          : {}),
      }];
    }).slice(0, 8)
    : [];
  const weatherRaw = isRecord(value.weather) ? value.weather : null;
  const weather = weatherRaw
    && typeof weatherRaw.location === "string"
    && finiteNumber(weatherRaw.temperature)
    && typeof weatherRaw.temperature_unit === "string"
    ? {
      location: weatherRaw.location,
      temperature: weatherRaw.temperature,
      temperatureUnit: weatherRaw.temperature_unit,
      ...(finiteNumber(weatherRaw.feels_like) ? { feelsLike: weatherRaw.feels_like } : {}),
      ...(finiteNumber(weatherRaw.humidity) ? { humidity: weatherRaw.humidity } : {}),
      ...(finiteNumber(weatherRaw.wind_speed) ? { windSpeed: weatherRaw.wind_speed } : {}),
      ...(typeof weatherRaw.wind_speed_unit === "string" ? { windSpeedUnit: weatherRaw.wind_speed_unit } : {}),
      ...(Number.isInteger(weatherRaw.weather_code) ? { weatherCode: weatherRaw.weather_code as number } : {}),
      ...(typeof weatherRaw.observed_at === "string" ? { observedAt: weatherRaw.observed_at } : {}),
      ...(typeof weatherRaw.sunset === "string" ? { sunset: weatherRaw.sunset } : {}),
    }
    : undefined;
  const stopwatchRaw = isRecord(value.stopwatch) ? value.stopwatch : null;
  const stopwatch = stopwatchRaw
    && (stopwatchRaw.state === "running" || stopwatchRaw.state === "stopped")
    && finiteNumber(stopwatchRaw.elapsed_seconds)
    && stopwatchRaw.elapsed_seconds >= 0
    && typeof stopwatchRaw.sampled_at === "string"
    && Number.isFinite(Date.parse(stopwatchRaw.sampled_at))
    ? { state: stopwatchRaw.state as "running" | "stopped", elapsedSeconds: stopwatchRaw.elapsed_seconds, sampledAt: stopwatchRaw.sampled_at }
    : undefined;
  const mediaRaw = isRecord(value.media) ? value.media : null;
  const mediaArtwork = typeof mediaRaw?.art_uri === "string"
    && mediaRaw.art_uri.length <= 2_000_000
    && /^data:image\/jpeg;base64,[A-Za-z0-9+/]+={0,2}$/.test(mediaRaw.art_uri)
    ? mediaRaw.art_uri
    : undefined;
  const media = mediaRaw
    && typeof mediaRaw.available === "boolean"
    && typeof mediaRaw.adapter_available === "boolean"
    && typeof mediaRaw.title === "string"
    && typeof mediaRaw.artist === "string"
    && typeof mediaRaw.status === "string"
    && finiteNumber(mediaRaw.position_seconds) && mediaRaw.position_seconds >= 0
    && finiteNumber(mediaRaw.duration_seconds) && mediaRaw.duration_seconds >= 0
    ? { available: mediaRaw.available, adapterAvailable: mediaRaw.adapter_available, title: mediaRaw.title, artist: mediaRaw.artist, ...(typeof mediaRaw.app === "string" ? { app: mediaRaw.app } : {}), ...(mediaArtwork ? { artUri: mediaArtwork } : {}), status: mediaRaw.status, positionSeconds: mediaRaw.position_seconds, durationSeconds: mediaRaw.duration_seconds }
    : undefined;
  const systemStatus = parseSystemStatus(value.system_status);
  return {
    clockVisible: value.clock_visible !== false,
    hiddenWidgets: Array.isArray(value.hidden_widgets)
      ? value.hidden_widgets.filter((id): id is string => typeof id === "string" && id.length <= 128 && /^(?:weather|stopwatch|music|system_status|timer|timer:.+)$/.test(id)).slice(0, 64)
      : [],
    timers,
    ...(systemStatus ? { systemStatus } : {}),
    ...(weather ? { weather } : {}),
    ...(stopwatch ? { stopwatch } : {}),
    ...(media ? { media } : {}),
  };
}

export function parseSnapshot(value: unknown): SceneSnapshot | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  if (!Number.isSafeInteger(raw.revision) || (raw.revision as number) < 0 || typeof raw.title !== "string") return null;
  if (raw.session_id !== undefined && typeof raw.session_id !== "string") return null;
  if (raw.summary !== undefined && typeof raw.summary !== "string") return null;
  if (raw.details !== undefined && (!Array.isArray(raw.details) || raw.details.some((item) =>
    !item || typeof item !== "object" || typeof (item as Record<string, unknown>).label !== "string" || typeof (item as Record<string, unknown>).value !== "string"))) return null;
  if (raw.tasks !== undefined && (!Array.isArray(raw.tasks) || raw.tasks.some((item) => !item || typeof item !== "object" || typeof (item as Record<string, unknown>).id !== "string" || typeof (item as Record<string, unknown>).title !== "string" || typeof (item as Record<string, unknown>).status !== "string"))) return null;
  if (raw.conversation_history !== undefined && !Array.isArray(raw.conversation_history)) return null;
  if (raw.activity !== undefined && !Array.isArray(raw.activity)) return null;
  const conversationHistory = Array.isArray(raw.conversation_history)
    ? raw.conversation_history.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const message = item as Record<string, unknown>;
      if (typeof message.id !== "string" || !["you", "charlie"].includes(String(message.role)) || typeof message.text !== "string") return [];
      return [{ id: message.id, role: message.role as "you" | "charlie", text: message.text, ...(typeof message.turn_id === "string" ? { turnId: message.turn_id } : {}) }];
    })
    : undefined;
  const activity = Array.isArray(raw.activity)
    ? raw.activity.flatMap((item) => {
      const event = parseRuntimeEvent(item);
      return event ? [event] : [];
    })
    : undefined;
  const widgets = parseDashboardWidgets(raw.widgets);
  const voice = raw.voice && typeof raw.voice === "object" ? raw.voice as Record<string, unknown> : null;
  const conversation = raw.conversation && typeof raw.conversation === "object" ? raw.conversation as Record<string, unknown> : null;
  if (raw.research_results !== undefined && !Array.isArray(raw.research_results)) return null;
  const latestResearch = parseResearchResult(raw.research_result);
  const parsedResearchResults = Array.isArray(raw.research_results)
    ? raw.research_results.flatMap((item) => {
      const result = parseResearchResult(item);
      return result ? [result] : [];
    })
    : [];
  const uniqueResearchResults = new Map<string, ResearchResultData>();
  for (const result of parsedResearchResults) {
    uniqueResearchResults.delete(result.id);
    uniqueResearchResults.set(result.id, result);
  }
  if (latestResearch) {
    uniqueResearchResults.delete(latestResearch.id);
    uniqueResearchResults.set(latestResearch.id, latestResearch);
  }
  const researchResults = [...uniqueResearchResults.values()].slice(-10);
  const research = latestResearch ?? researchResults[researchResults.length - 1];
  const settings = raw.settings && typeof raw.settings === "object" ? raw.settings as Record<string, unknown> : null;
  const approval = raw.approval && typeof raw.approval === "object"
    ? parseRuntimeEvent({ type: "tool_approval_request", payload: raw.approval }) : null;
  const settingFields: RuntimeSetting[] = [];
  if (settings?.authority === "main_runtime" && Array.isArray(settings.fields)) {
    for (const entry of settings.fields) {
      if (!entry || typeof entry !== "object") return null;
      const field = entry as Record<string, unknown>;
      if (typeof field.key !== "string" || typeof field.group !== "string" || typeof field.label !== "string" || typeof field.secret !== "boolean" || !["bool", "int", "float", "list", "str"].includes(String(field.type))) return null;
      const value = field.value;
      if (value !== null && typeof value !== "string" && typeof value !== "boolean" && !(typeof value === "number" && Number.isFinite(value)) && !(Array.isArray(value) && value.every((item) => typeof item === "string"))) return null;
      settingFields.push({ key: field.key, group: field.group, label: field.label, type: field.type as RuntimeSetting["type"], secret: field.secret, value: field.secret ? null : value as RuntimeSetting["value"], isSet: field.is_set === true, pending: field.pending === true });
    }
  }
  return {
    revision: raw.revision as number,
    title: raw.title,
    ...(typeof raw.session_id === "string" ? { sessionId: raw.session_id } : {}),
    ...(typeof raw.summary === "string" ? { summary: raw.summary } : {}),
    details: (raw.details as SceneSnapshot["details"] | undefined) ?? [],
    ...(conversationHistory ? { conversationHistory } : {}),
    ...(activity ? { activity } : {}),
    ...(widgets ? { widgets } : {}),
    ...(conversation && (conversation.state === "working" || conversation.state === "ready") ? { conversationState: conversation.state } : {}),
    ...(typeof raw.active_turn_id === "string" || raw.active_turn_id === null ? { activeTurnId: raw.active_turn_id as string | null } : {}),
    ...(typeof raw.active_task_id === "string" || raw.active_task_id === null ? { activeTaskId: raw.active_task_id as string | null } : {}),
    ...(settings ? { settings: settingFields } : {}),
    ...(approval?.channel === "web" && approval.requestId ? { pendingApproval: approval } : {}),
    ...(voice && typeof voice.enabled === "boolean" && typeof voice.mic_muted === "boolean" && typeof voice.muted === "boolean" && typeof voice.volume === "number" && Number.isFinite(voice.volume) ? { voice: { enabled: voice.enabled, mic_muted: voice.mic_muted, muted: voice.muted, volume: voice.volume } } : {}),
    ...(research ? { researchResult: research } : {}),
    ...(researchResults.length ? { researchResults } : {}),
    ...(Array.isArray(raw.tasks) ? { tasks: raw.tasks.map((item) => {
      const task = item as Record<string, unknown>;
      return {
        id: task.id as string,
        title: task.title as string,
        status: task.status as string,
        ...(typeof task.current_action === "string" ? { currentAction: task.current_action } : {}),
        ...(typeof task.error_summary === "string" ? { errorSummary: task.error_summary } : {}),
        ...(typeof task.updated_at === "string" ? { updatedAt: task.updated_at } : {}),
      };
    }) } : {}),
  };
}

export function parseRuntimeEvent(value: unknown): RuntimeEvent | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  if (typeof raw.type !== "string" || !raw.type) return null;
  const payload = raw.payload && typeof raw.payload === "object" ? raw.payload as Record<string, unknown> : {};
  const snapshot = parseSnapshot(raw.snapshot ?? payload.snapshot);
  const eventText = [payload.text, payload.transcript, payload.message, payload.token].find((value): value is string => typeof value === "string" && value.trim().length > 0);
  const eventLevel = typeof payload.level === "number" && Number.isFinite(payload.level) ? Math.max(0, Math.min(1, payload.level)) : undefined;
  const eventSummary = [payload.title, payload.current_action, payload.action, payload.message, payload.name, payload.text]
    .find((value): value is string => typeof value === "string" && value.trim().length > 0);
  const taskId = typeof raw.task_id === "string" ? raw.task_id : typeof payload.task_id === "string" ? payload.task_id : undefined;
  const turnId = typeof raw.turn_id === "string" ? raw.turn_id : typeof payload.turn_id === "string" ? payload.turn_id : undefined;
  const researchResult = raw.type === "research_result" ? parseResearchResult(payload) : undefined;
  const widgets = raw.type === "dashboard_widgets" ? parseDashboardWidgets(payload.widgets) : undefined;
  const systemStatus = raw.type === "system_status" ? parseSystemStatus(payload) : undefined;
  const count = (key: string) => typeof payload[key] === "number" && Number.isSafeInteger(payload[key]) && Number(payload[key]) >= 0 ? Number(payload[key]) : undefined;
  return {
    type: raw.type,
    ...(count("source_count") !== undefined ? { sourceCount: count("source_count") } : {}),
    ...(count("evidence_count") !== undefined ? { evidenceCount: count("evidence_count") } : {}),
    ...(count("finding_count") !== undefined ? { findingCount: count("finding_count") } : {}),
    ...(typeof raw.id === "string" ? { id: raw.id } : {}),
    ...(typeof raw.source === "string" ? { source: raw.source } : {}),
    ...(typeof raw.timestamp === "string" ? { timestamp: raw.timestamp } : {}),
    ...(snapshot ? { snapshot } : {}),
    ...(eventText ? { text: eventText } : {}),
    ...(typeof payload.partial === "boolean" ? { partial: payload.partial } : {}),
    ...(eventLevel !== undefined ? { level: eventLevel } : {}),
    ...(eventSummary ? { summary: eventSummary } : {}),
    ...(typeof payload.stage === "string" ? { stage: payload.stage } : {}),
    ...(typeof payload.query === "string" ? { query: payload.query } : {}),
    ...(taskId !== undefined ? { taskId } : {}),
    ...(turnId !== undefined ? { turnId } : {}),
    ...(typeof raw.session_id === "string" ? { sessionId: raw.session_id } : typeof payload.session_id === "string" ? { sessionId: payload.session_id } : {}),
    ...(typeof payload.result_id === "string" ? { resultId: payload.result_id } : {}),
    ...(typeof payload.request_id === "string" ? { requestId: payload.request_id } : {}),
    ...(["voice", "telegram", "web", "console"].includes(String(payload.channel)) ? {
      channel: payload.channel as RuntimeEvent["channel"],
    } : {}),
    ...(typeof payload.operation_preview === "string" ? { operationPreview: payload.operation_preview } : {}),
    ...(researchResult ? { researchResult: turnId !== undefined ? { ...researchResult, turnId } : researchResult } : {}),
    ...(widgets ? { widgets } : {}),
    ...(systemStatus ? { systemStatus } : {}),
  };
}

export async function loadScene(fetcher: typeof fetch = fetch): Promise<SceneSnapshot> {
  const response = await fetcher("/api/scene", { credentials: "same-origin", cache: "no-store" });
  if (!response.ok) throw new Error(`Scene endpoint returned HTTP ${response.status}.`);
  const snapshot = parseSnapshot(await response.json());
  if (!snapshot) throw new Error("Scene endpoint returned an unsupported scene snapshot.");
  return snapshot;
}

export async function postRuntimeCommand(command: Record<string, unknown>, fetcher: typeof fetch = fetch): Promise<boolean> {
  const response = await fetcher("/api/commands", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(command),
  });
  if (!response.ok) {
    let detail = "";
    try {
      const payload: unknown = await response.json();
      if (payload && typeof payload === "object" && typeof (payload as Record<string, unknown>).error === "string") detail = `: ${(payload as Record<string, unknown>).error}`;
    } catch {
      // Keep the HTTP status when the gateway did not return JSON.
    }
    throw new Error(`Command endpoint returned HTTP ${response.status}${detail}.`);
  }
  const result: unknown = await response.json();
  if (result && typeof result === "object" && (result as Record<string, unknown>).accepted === false) {
    const error = (result as Record<string, unknown>).error;
    throw new Error(typeof error === "string" ? error : "The runtime rejected the command.");
  }
  if (!result || typeof result !== "object") return false;
  const ack = result as Record<string, unknown>;
  return ack.accepted === true || ack.status === "accepted";
}

const pendingTextCommands = new Map<string, string>();

export function pendingTextCommandId(text: string, sessionId: string): string {
  const key = `${sessionId}\0${text.trim()}`;
  const existing = pendingTextCommands.get(key);
  if (existing) {
    pendingTextCommands.delete(key);
    pendingTextCommands.set(key, existing);
    return existing;
  }
  const requestId = crypto.randomUUID();
  pendingTextCommands.set(key, requestId);
  if (pendingTextCommands.size > 32) pendingTextCommands.delete(pendingTextCommands.keys().next().value!);
  return requestId;
}

export async function postCommand(text: string, fetcher: typeof fetch = fetch, sessionId = ""): Promise<boolean> {
  const cleanText = text.trim();
  const requestId = pendingTextCommandId(cleanText, sessionId);
  const accepted = await postRuntimeCommand({
    type: "submit_text", text: cleanText, session_id: sessionId, request_id: requestId,
  }, fetcher);
  if (accepted) pendingTextCommands.delete(`${sessionId}\0${cleanText}`);
  return accepted;
}
