import type { CoreState } from "../components/CharlieCore";
import type { EvidenceClass } from "../research/payload";
import type { RuntimeWidgetState } from "./widgets";

export interface RuntimeEvent {
  type: string;
  version?: number;
  id?: string;
  timestamp?: string;
  source?: string;
  payload?: Record<string, unknown>;
  session_id?: string | null;
  task_id?: string | null;
  turn_id?: string | null;
  correlation_id?: string | null;
  replay?: boolean;
  [key: string]: unknown;
}

export type RuntimeConnection =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected"
  | "degraded";

export interface RuntimeTask {
  id: string;
  title?: string;
  status?: string;
  current_action?: string;
  currentAction?: string;
  progress?: number;
  [key: string]: unknown;
}

export interface RuntimeApproval {
  requestId: string;
  toolName: string;
  reason: string;
  riskClass?: string;
  arguments?: Record<string, unknown>;
  taskId?: string | null;
  turnId?: string | null;
}

export interface RuntimePresentation {
  id: string;
  kind: string;
  title?: string;
  summary?: string;
  workspaceType?: string;
  priority?: number;
  content?: unknown;
  taskId?: string | null;
  sessionId?: string | null;
  turnId?: string | null;
  correlationId?: string | null;
  [key: string]: unknown;
}

export interface RuntimeConversation {
  userText: string;
  responseText: string;
  streaming: boolean;
  turnId: string | null;
}

export interface RuntimeActivity {
  kind: "tool" | "task" | "terminal" | "research";
  label: string;
  detail?: string;
  taskId?: string | null;
}

export interface RuntimeResult {
  kind: "tool" | "terminal" | "research" | "task" | "presentation";
  title: string;
  summary: string;
  detail?: string;
  taskId?: string | null;
}

export interface RuntimeResearch {
  progress: {
    stage: string;
    message: string;
    current?: number;
    total?: number;
    mode?: string;
  } | null;
  result: Record<string, unknown> | null;
  objective?: string | null;
  sessionId?: string | null;
  taskId?: string | null;
  turnId?: string | null;
  correlationId?: string | null;
  activity?: ResearchActivity[];
  updatedAt?: string | null;
  error?: string | null;
  truth?: EvidenceClass;
  presentationId?: string | null;
}

export interface ResearchActivity {
  id: string;
  stage: string;
  message: string;
  current?: number;
  total?: number;
  mode?: string;
}

export interface RuntimeMediaSnapshot {
  available: boolean;
  title?: string;
  artist?: string;
  album?: string;
  app?: string;
  status?: string;
  position_seconds?: number;
  duration_seconds?: number;
  art_uri?: string | null;
  volume_percent?: number | null;
  muted?: boolean | null;
  reason?: string;
  [key: string]: unknown;
}

export interface RuntimeState {
  connection: RuntimeConnection;
  sessionId: string | null;
  coreState: CoreState;
  conversation: RuntimeConversation;
  transcript: string;
  thinking: string;
  activeActivity: RuntimeActivity | null;
  lastResult: RuntimeResult | null;
  tasks: Record<string, RuntimeTask>;
  approvals: Record<string, RuntimeApproval>;
  inFlightApprovals: Record<string, boolean>;
  research: RuntimeResearch;
  terminal: Record<string, unknown> | null;
  systemStatus: Record<string, unknown>;
  subsystemHealth: Record<string, unknown>;
  runtimeTruth: Record<string, unknown> | null;
  telemetry: Record<string, unknown> | null;
  presentations: Record<string, RuntimePresentation>;
  widgets: Record<string, RuntimeWidgetState>;
  media: RuntimeMediaSnapshot | null;
  mediaPending: boolean;
  mediaError: string | null;
  audioState: Record<string, unknown>;
  micState: Record<string, unknown>;
  lastAlert: string | null;
  lastError: string | null;
  lastEventType: string | null;
  seenEventIds: string[];
}

export interface RuntimeActions {
  approve: (requestId: string) => boolean;
  reject: (requestId: string) => boolean;
  dismiss: (id: string) => boolean;
  sendChat?: (text: string) => boolean;
  focusTask?: (taskId: string) => boolean;
  mediaControl?: (action: MediaAction) => Promise<boolean>;
}

export type MediaAction = "play_pause" | "next_track" | "prev_track" | "volume_up" | "volume_down" |
  "set_volume" | "mute" | "unmute" | "stop";
