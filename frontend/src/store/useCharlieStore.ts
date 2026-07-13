import { create } from "zustand";

export type VoiceState = "idle" | "listening" | "thinking" | "speaking";

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

export interface Task {
  id: string;
  name: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled";
  assigned_to?: string;
  column?: "backlog" | "todo" | "in_progress" | "done";
  priority?: number;
  dependencies?: string[];
  parent_task_id?: string | null;
  result?: string;
  retry_count?: number;
  approval_status?: "pending_approval" | "approved" | "rejected";
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

export interface Agent {
  name: string;
  role: string;
  current_task?: string;
  status: string;
}

export interface BlackboardState {
  tasks: Task[];
  agents: Record<string, Agent>;
}

export interface SystemStatus {
  cpu: number;
  ram: number;
  gpu: number;
  active_agents: string[];
}

export interface AudioState {
  muted: boolean;
  volume: number;
}

export interface MicState {
  mic_muted: boolean;
}

export interface ToolActivityEntry {
  kind: "tool_call" | "tool_result" | "thinking_update";
  name: string;
  text: string;
  sessionId?: string;
}

export interface Alert {
  severity: "info" | "warn" | "error";
  message: string;
  timestamp: string;
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
  blackboard: BlackboardState;
  voiceState: VoiceState;
  audio: AudioState;
  mic: MicState;
  audioLevel: number;
  toolActivity: ToolActivityEntry[];
  launchId: string;
  sessionScope: "all" | "this_launch";
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
  setBlackboard: (b: BlackboardState) => void;
  setVoiceState: (s: VoiceState) => void;
  setAudio: (a: AudioState) => void;
  setMic: (m: MicState) => void;
  setAudioLevel: (level: number) => void;
  appendToolActivity: (e: ToolActivityEntry) => void;
  clearToolActivity: () => void;
  setLaunchId: (id: string) => void;
  setSessionScope: (scope: "all" | "this_launch") => void;
  setAccentColor: (color: string) => void;
  activeProposal: RecoveryProposal | null;
  setActiveProposal: (p: RecoveryProposal | null) => void;
}

export const useCharlieStore = create<CharlieState>((set) => ({
  connected: false,
  systemStatus: { cpu: 0, ram: 0, gpu: 0, active_agents: [] },
  sessions: [],
  currentSessionId: "",
  messages: [],
  messagesLoading: false,
  alerts: [],
  logs: [],
  blackboard: { tasks: [], agents: {} },
  voiceState: "idle",
  audio: { muted: false, volume: 1.0 },
  mic: { mic_muted: false },
  audioLevel: 0,
  toolActivity: [],
  launchId: "",
  sessionScope: "all",
  accentColor: typeof window !== "undefined" ? localStorage.getItem("charlie_accent") || "#a855f7" : "#a855f7",

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
  setBlackboard: (blackboard) => set({ blackboard }),
  setVoiceState: (voiceState: VoiceState) => set({ voiceState }),
  setAudio: (audio) => set({ audio }),
  setMic: (mic) => set({ mic }),
  setAudioLevel: (audioLevel) => set({ audioLevel }),
  appendToolActivity: (e) => set((st) => ({ toolActivity: [...st.toolActivity, e] })),
  clearToolActivity: () => set({ toolActivity: [] }),
  setLaunchId: (launchId) => set({ launchId }),
  setSessionScope: (sessionScope) => set({ sessionScope }),
  setAccentColor: (color) => set(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem("charlie_accent", color);
    }
    return { accentColor: color };
  }),
  activeProposal: null,
  setActiveProposal: (activeProposal) => set({ activeProposal }),
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
