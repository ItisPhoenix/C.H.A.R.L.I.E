import { create } from "zustand";

export type VoiceState = "idle" | "listening" | "thinking" | "speaking";
// What triggered the current "listening" state -- the backend emits vad_start
// and wake_word as distinct events, but both used to collapse into the same
// UI state with no way to tell them apart. Kept separate from VoiceState
// itself so existing voiceState consumers (VoiceDock, etc.) don't need a new
// case; read this only where the distinction actually matters.
export type ListeningTrigger = "vad" | "wake_word" | null;

export interface Session {
  id: string;
  title: string;
  created_at: string;
}

export interface Message {
  id?: string;
  role: "user" | "assistant" | "system";
  content: string;
}

export interface RecoveryProposal {
  proposal_id: string;
  original_command: string;
  proposed_command: string;
  failure_class: string;
  explanation: string;
  source: string;
  safeguard_passed: boolean;
  session_id: string;
}

export interface ToolApprovalRequest {
  request_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  reason: string;
  session_id: string;
}

export interface SystemStatus {
  cpu: number;
  ram: number;
  gpu: number;
}

export interface AudioState {
  muted: boolean;
  volume: number;
}

export interface MicState {
  mic_muted: boolean;
}

export interface ToolActivityEntry {
  kind: "tool_call" | "tool_result" | "thinking_update" | "agent_spawned" | "agent_status" | "agent_result";
  name: string;
  text: string;
  sessionId?: string;
}

export type AgentRunStatus = "running" | "done" | "timeout" | "cancelled";

export interface AgentRun {
  agentId: string;
  task: string;
  status: AgentRunStatus;
  lastTool?: string;
  result?: string;
  spawnedAt: number;
  finishedAt?: number;
  sessionId?: string;
}

export interface Alert {
  severity: "info" | "warn" | "error";
  message: string;
  timestamp: string;
}

export interface DesktopFrameMark {
  mark_id: number;
  name: string;
  bounds: number[];
}

export interface DesktopFrame {
  sessionId: string;
  imageB64: string;
  marks: DesktopFrameMark[];
  receivedAt: number;
}

interface CharlieState {
  connected: boolean;
  systemStatus: SystemStatus;
  sessions: Session[];
  currentSessionId: string;
  messages: Message[];
  messagesLoading: boolean;
  alerts: Alert[];
  logs: string[];
  voiceState: VoiceState;
  listeningTrigger: ListeningTrigger;
  audio: AudioState;
  mic: MicState;
  audioLevel: number;
  toolActivity: ToolActivityEntry[];
  agentRuns: AgentRun[];
  launchId: string;
  accentColor: string;

  setConnected: (c: boolean) => void;
  setSystemStatus: (s: SystemStatus) => void;
  setSessions: (s: Session[]) => void;
  setCurrentSessionId: (id: string) => void;
  setMessages: (m: Message[]) => void;
  addMessage: (m: Message) => void;
  updateLastMessageContent: (content: string) => void;
  setMessagesLoading: (l: boolean) => void;
  addAlert: (a: Alert) => void;
  addLog: (l: string) => void;
  setVoiceState: (s: VoiceState) => void;
  setListeningTrigger: (t: ListeningTrigger) => void;
  setAudio: (a: AudioState) => void;
  setMic: (m: MicState) => void;
  setAudioLevel: (level: number) => void;
  appendToolActivity: (e: ToolActivityEntry) => void;
  clearToolActivity: () => void;
  upsertAgentRun: (patch: Partial<AgentRun> & { agentId: string }) => void;
  setLaunchId: (id: string) => void;
  setAccentColor: (color: string) => void;
  activeProposal: RecoveryProposal | null;
  setActiveProposal: (p: RecoveryProposal | null) => void;
  activeToolApproval: ToolApprovalRequest | null;
  setActiveToolApproval: (r: ToolApprovalRequest | null) => void;
  latestDesktopFrame: DesktopFrame | null;
  setLatestDesktopFrame: (f: DesktopFrame | null) => void;
  desktopControlEnabled: boolean;
  setDesktopControlEnabled: (enabled: boolean) => void;
  selectedFileContent: string;
  setSelectedFileContent: (content: string) => void;
}

export const useCharlieStore = create<CharlieState>((set) => ({
  connected: false,
  systemStatus: { cpu: 0, ram: 0, gpu: 0 },
  sessions: [],
  currentSessionId: "",
  messages: [],
  messagesLoading: false,
  alerts: [],
  logs: [],
  voiceState: "idle",
  listeningTrigger: null,
  audio: { muted: false, volume: 1.0 },
  mic: { mic_muted: false },
  audioLevel: 0,
  toolActivity: [],
  agentRuns: [],
  launchId: "",
  // Always starts at the default; the persisted value (if any) is applied after mount
  // (see page.tsx) so the first client render matches the server-rendered HTML.
  accentColor: "#a855f7",

  setConnected: (connected) => set({ connected }),
  setSystemStatus: (systemStatus) => set({ systemStatus }),
  setSessions: (sessions) => set({ sessions }),
  setCurrentSessionId: (currentSessionId) => set({ currentSessionId }),
  // Replace the message list with server history. fetchMessages re-pulls on every
  // session switch with freshly generated ids, so a merge would accumulate the
  // previous session's messages and break session isolation. Replace wholesale.
  setMessages: (messages) => set({ messages }),
  addMessage: (msg) => set((state) => ({ messages: [...state.messages, { id: crypto.randomUUID(), ...msg }] })),
  // Append a streamed token to the last assistant message. The backend emits
  // complete sentences (one "token" event per sentence), so we accumulate the
  // running answer into a single growing bubble instead of replacing it.
  updateLastMessageContent: (token) => set((state) => {
    const copy = [...state.messages];
    if (copy.length > 0 && copy[copy.length - 1].role === "assistant") {
      const prev = copy[copy.length - 1];
      copy[copy.length - 1] = { ...prev, content: prev.content + token };
    } else {
      copy.push({ id: crypto.randomUUID(), role: "assistant", content: token });
    }
    return { messages: copy };
  }),
  setMessagesLoading: (messagesLoading) => set({ messagesLoading }),
  addAlert: (alert) => set((state) => ({ alerts: [alert, ...state.alerts].slice(0, 100) })),
  addLog: (log) => set((state) => ({ logs: [log, ...state.logs].slice(0, 500) })),
  setVoiceState: (voiceState: VoiceState) => set({ voiceState }),
  setListeningTrigger: (listeningTrigger: ListeningTrigger) => set({ listeningTrigger }),
  setAudio: (audio) => set({ audio }),
  setMic: (mic) => set({ mic }),
  setAudioLevel: (audioLevel) => set({ audioLevel }),
  appendToolActivity: (e) => set((st) => ({ toolActivity: [...st.toolActivity, e] })),
  clearToolActivity: () => set({ toolActivity: [] }),
  // Merge-by-agentId so agent_spawned/agent_status/agent_result updates the same
  // run entry instead of appending duplicates; caps history like alerts/logs.
  upsertAgentRun: (patch) => set((st) => {
    const idx = st.agentRuns.findIndex((r) => r.agentId === patch.agentId);
    if (idx === -1) {
      const created: AgentRun = {
        task: "",
        status: "running",
        spawnedAt: Date.now(),
        ...patch,
      };
      return { agentRuns: [created, ...st.agentRuns].slice(0, 100) };
    }
    const copy = [...st.agentRuns];
    copy[idx] = { ...copy[idx], ...patch };
    return { agentRuns: copy };
  }),
  setLaunchId: (launchId) => set({ launchId }),
  setAccentColor: (color) => set(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem("charlie_accent", color);
      applyAccentColor(color);
    }
    return { accentColor: color };
  }),
  activeProposal: null,
  setActiveProposal: (activeProposal) => set({ activeProposal }),
  activeToolApproval: null,
  setActiveToolApproval: (activeToolApproval) => set({ activeToolApproval }),
  latestDesktopFrame: null,
  setLatestDesktopFrame: (latestDesktopFrame) => set({ latestDesktopFrame }),
  desktopControlEnabled: false,
  setDesktopControlEnabled: (desktopControlEnabled) => set({ desktopControlEnabled }),
  selectedFileContent: "",
  setSelectedFileContent: (selectedFileContent) => set({ selectedFileContent }),
}));

export function hexToRgb(hex: string) {
  const h = hex.replace("#", "");
  const n = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const num = parseInt(n, 16);
  return { r: (num >> 16) & 255, g: (num >> 8) & 255, b: num & 255 };
}

export function rgba(hex: string, a: number): string {
  const { r, g, b } = hexToRgb(hex);
  return `rgba(${r},${g},${b},${a})`;
}

export function lighten(hex: string, amt: number): string {
  const { r, g, b } = hexToRgb(hex);
  const l = (c: number) => Math.min(255, Math.round(c + (255 - c) * amt));
  return `rgb(${l(r)},${l(g)},${l(b)})`;
}

/** Writes the live accent color onto :root as real CSS custom properties
 * (globals.css declares matching @theme proxies) so every bg-accent/
 * text-accent/border-accent utility across the app updates together,
 * instead of each consumer tracking accentColor state independently. */
export function applyAccentColor(color: string): void {
  const root = document.documentElement.style;
  root.setProperty("--accent", color);
  root.setProperty("--accent-dim", rgba(color, 0.12));
  root.setProperty("--accent-border", rgba(color, 0.25));
  root.setProperty("--accent-soft", lighten(color, 0.35));
}
