import type { OrbState } from "thinking-orbs";

export type SceneSnapshot = {
  revision: number;
  title: string;
  summary?: string;
  details: Array<{ label: string; value: string }>;
  tasks?: RuntimeTask[];
};

export type RuntimeTask = {
  id: string;
  title: string;
  status: string;
  currentAction?: string;
  errorSummary?: string;
  updatedAt?: string;
};

export function orbStateForEvent(type: string): OrbState | null {
  if (["transcript", "vad_start", "ptt_start"].includes(type)) return "listening";
  if (["thinking", "thinking_update", "tool_call", "research_progress", "background_task"].includes(type)) return "working";
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
  level?: number;
  summary?: string;
};

export function parseSnapshot(value: unknown): SceneSnapshot | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  if (!Number.isSafeInteger(raw.revision) || (raw.revision as number) < 0 || typeof raw.title !== "string") return null;
  if (raw.summary !== undefined && typeof raw.summary !== "string") return null;
  if (raw.details !== undefined && (!Array.isArray(raw.details) || raw.details.some((item) =>
    !item || typeof item !== "object" || typeof (item as Record<string, unknown>).label !== "string" || typeof (item as Record<string, unknown>).value !== "string"))) return null;
  if (raw.tasks !== undefined && (!Array.isArray(raw.tasks) || raw.tasks.some((item) => !item || typeof item !== "object" || typeof (item as Record<string, unknown>).id !== "string" || typeof (item as Record<string, unknown>).title !== "string" || typeof (item as Record<string, unknown>).status !== "string"))) return null;
  return {
    revision: raw.revision as number,
    title: raw.title,
    ...(typeof raw.summary === "string" ? { summary: raw.summary } : {}),
    details: (raw.details as SceneSnapshot["details"] | undefined) ?? [],
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
    ...(eventLevel !== undefined ? { level: eventLevel } : {}),
    ...(eventSummary ? { summary: eventSummary } : {}),
  };
}

export async function loadScene(fetcher: typeof fetch = fetch): Promise<SceneSnapshot> {
  const response = await fetcher("/api/scene", { credentials: "same-origin", cache: "no-store" });
  if (!response.ok) throw new Error(`Scene endpoint returned HTTP ${response.status}.`);
  const snapshot = parseSnapshot(await response.json());
  if (!snapshot) throw new Error("Scene endpoint returned an unsupported scene snapshot.");
  return snapshot;
}

export async function postCommand(text: string, fetcher: typeof fetch = fetch): Promise<boolean> {
  const response = await fetcher("/api/commands", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type: "submit_text", text }),
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
  if (!result || typeof result !== "object") return false;
  const ack = result as Record<string, unknown>;
  return ack.accepted === true || ack.status === "accepted";
}
