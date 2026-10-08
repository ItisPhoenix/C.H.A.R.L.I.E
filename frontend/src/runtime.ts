import type { OrbState } from "thinking-orbs";

export type SceneSnapshot = {
  revision: number;
  title: string;
  summary?: string;
  details: Array<{ label: string; value: string }>;
  tasks?: RuntimeTask[];
  conversationState?: "working" | "ready";
  activeTurnId?: string | null;
  activeTaskId?: string | null;
  voice?: { enabled: boolean; mic_muted: boolean; muted: boolean; volume: number };
  researchResult?: ResearchResultData;
  settings?: RuntimeSetting[];
  pendingApproval?: RuntimeEvent;
};

export type ResearchSource = {
  id: string;
  title: string;
  url: string;
  class?: string;
};

export type ResearchResultData = {
  id: string;
  text: string;
  query: string;
  answer?: string;
  partial?: boolean;
  synthesis?: string;
  gaps?: string[];
  sources?: ResearchSource[];
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

export function orbStateForEvent(event: string | Pick<RuntimeEvent, "type" | "stage">): OrbState | null {
  const { type, stage } = typeof event === "string" ? { type: event, stage: undefined } : event;
  if (["transcript", "vad_start", "ptt_start"].includes(type)) return "listening";
  if (type === "research_progress") {
    if (stage === "planning") return "weaving";
    if (stage === "found") return "shaping";
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
  resultId?: string;
  requestId?: string;
  channel?: "voice" | "telegram" | "web" | "console";
  operationPreview?: string;
  researchResult?: ResearchResultData;
};

export function parseSnapshot(value: unknown): SceneSnapshot | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  if (!Number.isSafeInteger(raw.revision) || (raw.revision as number) < 0 || typeof raw.title !== "string") return null;
  if (raw.summary !== undefined && typeof raw.summary !== "string") return null;
  if (raw.details !== undefined && (!Array.isArray(raw.details) || raw.details.some((item) =>
    !item || typeof item !== "object" || typeof (item as Record<string, unknown>).label !== "string" || typeof (item as Record<string, unknown>).value !== "string"))) return null;
  if (raw.tasks !== undefined && (!Array.isArray(raw.tasks) || raw.tasks.some((item) => !item || typeof item !== "object" || typeof (item as Record<string, unknown>).id !== "string" || typeof (item as Record<string, unknown>).title !== "string" || typeof (item as Record<string, unknown>).status !== "string"))) return null;
  const voice = raw.voice && typeof raw.voice === "object" ? raw.voice as Record<string, unknown> : null;
  const conversation = raw.conversation && typeof raw.conversation === "object" ? raw.conversation as Record<string, unknown> : null;
  const research = raw.research_result && typeof raw.research_result === "object" ? raw.research_result as Record<string, unknown> : null;
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
    ...(typeof raw.summary === "string" ? { summary: raw.summary } : {}),
    details: (raw.details as SceneSnapshot["details"] | undefined) ?? [],
    ...(conversation && (conversation.state === "working" || conversation.state === "ready") ? { conversationState: conversation.state } : {}),
    ...(typeof raw.active_turn_id === "string" || raw.active_turn_id === null ? { activeTurnId: raw.active_turn_id as string | null } : {}),
    ...(typeof raw.active_task_id === "string" || raw.active_task_id === null ? { activeTaskId: raw.active_task_id as string | null } : {}),
    ...(settings ? { settings: settingFields } : {}),
    ...(approval?.channel === "web" && approval.requestId ? { pendingApproval: approval } : {}),
    ...(voice && typeof voice.enabled === "boolean" && typeof voice.mic_muted === "boolean" && typeof voice.muted === "boolean" && typeof voice.volume === "number" && Number.isFinite(voice.volume) ? { voice: { enabled: voice.enabled, mic_muted: voice.mic_muted, muted: voice.muted, volume: voice.volume } } : {}),
    ...(research && typeof research.result_id === "string" && typeof research.text === "string" && typeof research.query === "string" ? {
      researchResult: {
        id: research.result_id,
        text: research.text,
        query: research.query,
        ...(typeof research.answer === "string" ? { answer: research.answer } : {}),
        ...(typeof research.partial === "boolean" ? { partial: research.partial } : {}),
        ...(typeof research.synthesis === "string" ? { synthesis: research.synthesis } : {}),
        ...(Array.isArray(research.gaps) ? { gaps: research.gaps.filter((g): g is string => typeof g === "string") } : {}),
        ...(Array.isArray(research.sources) ? {
          sources: research.sources.flatMap((s) => {
            if (!s || typeof s !== "object") return [];
            const src = s as Record<string, unknown>;
            if (typeof src.id !== "string" || typeof src.title !== "string" || typeof src.url !== "string") return [];
            return [{
              id: src.id,
              title: src.title,
              url: src.url,
              ...(typeof src.class === "string" ? { class: src.class } : {}),
            }];
          })
        } : {}),
        ...(typeof research.completed_at === "string" ? { completedAt: research.completed_at } : {}),
      }
    } : {}),
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
  return {
    type: raw.type,
    ...(typeof raw.id === "string" ? { id: raw.id } : {}),
    ...(typeof raw.source === "string" ? { source: raw.source } : {}),
    ...(typeof raw.timestamp === "string" ? { timestamp: raw.timestamp } : {}),
    ...(snapshot ? { snapshot } : {}),
    ...(eventText ? { text: eventText } : {}),
    ...(typeof payload.partial === "boolean" ? { partial: payload.partial } : {}),
    ...(eventLevel !== undefined ? { level: eventLevel } : {}),
    ...(eventSummary ? { summary: eventSummary } : {}),
    ...(typeof payload.stage === "string" ? { stage: payload.stage } : {}),
    ...(typeof payload.result_id === "string" ? { resultId: payload.result_id } : {}),
    ...(typeof payload.request_id === "string" ? { requestId: payload.request_id } : {}),
    ...(["voice", "telegram", "web", "console"].includes(String(payload.channel)) ? {
      channel: payload.channel as RuntimeEvent["channel"],
    } : {}),
    ...(typeof payload.operation_preview === "string" ? { operationPreview: payload.operation_preview } : {}),
    ...(raw.type === "research_result" && typeof payload.result_id === "string" && typeof payload.text === "string" ? {
      researchResult: {
        id: payload.result_id,
        text: payload.text,
        query: typeof payload.query === "string" ? payload.query : "",
        ...(typeof payload.answer === "string" ? { answer: payload.answer } : {}),
        ...(typeof payload.partial === "boolean" ? { partial: payload.partial } : {}),
        ...(typeof payload.synthesis === "string" ? { synthesis: payload.synthesis } : {}),
        ...(Array.isArray(payload.gaps) ? { gaps: payload.gaps.filter((g): g is string => typeof g === "string") } : {}),
        ...(Array.isArray(payload.sources) ? {
          sources: payload.sources.flatMap((s) => {
            if (!s || typeof s !== "object") return [];
            const src = s as Record<string, unknown>;
            if (typeof src.id !== "string" || typeof src.title !== "string" || typeof src.url !== "string") return [];
            return [{
              id: src.id,
              title: src.title,
              url: src.url,
              ...(typeof src.class === "string" ? { class: src.class } : {}),
            }];
          })
        } : {}),
        ...(typeof payload.completed_at === "string" ? { completedAt: payload.completed_at } : {}),
      }
    } : {}),
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

export function postCommand(text: string, fetcher: typeof fetch = fetch): Promise<boolean> {
  return postRuntimeCommand({ type: "submit_text", text }, fetcher);
}
