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
  description: string;
  status: "pending" | "running" | "done" | "failed";
  agent?: string;
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
  searchQuery: string;
  alerts: Alert[];
  logs: string[];
  activeTab: string;
  blackboard: BlackboardState;
  voiceState: VoiceState;
  audio: AudioState;
  mic: MicState;
  audioLevel: number;

  setConnected: (c: boolean) => void;
  setSystemStatus: (s: SystemStatus) => void;
  setSessions: (s: Session[]) => void;
  setCurrentSessionId: (id: string) => void;
  setMessages: (m: Message[]) => void;
  addMessage: (m: Message) => void;
  updateLastMessageContent: (content: string) => void;
  setMessagesLoading: (l: boolean) => void;
  setSearchQuery: (q: string) => void;
  addAlert: (a: Alert) => void;
  addLog: (l: string) => void;
  setActiveTab: (t: string) => void;
  setBlackboard: (b: BlackboardState) => void;
  setVoiceState: (s: VoiceState) => void;
  setAudio: (a: AudioState) => void;
  setMic: (m: MicState) => void;
  setAudioLevel: (level: number) => void;
}

export const useCharlieStore = create<CharlieState>((set) => ({
  connected: false,
  systemStatus: { cpu: 0, ram: 0, gpu: 0, active_agents: [] },
  sessions: [],
  currentSessionId: "",
  messages: [],
  messagesLoading: false,
  searchQuery: "",
  alerts: [],
  logs: [],
  activeTab: "dashboard",
  blackboard: { tasks: [], agents: {} },
  voiceState: "idle",
  audio: { muted: false, volume: 1.0 },
  mic: { mic_muted: false },
  audioLevel: 0,

  setConnected: (connected) => set({ connected }),
  setSystemStatus: (systemStatus) => set({ systemStatus }),
  setSessions: (sessions) => set({ sessions }),
  setCurrentSessionId: (currentSessionId) => set({ currentSessionId }),
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
  setSearchQuery: (searchQuery) => set({ searchQuery }),
  addAlert: (alert) => set((state) => ({ alerts: [alert, ...state.alerts].slice(0, 100) })),
  addLog: (log) => set((state) => ({ logs: [log, ...state.logs].slice(0, 500) })),
  setActiveTab: (activeTab) => set({ activeTab }),
  setBlackboard: (blackboard) => set({ blackboard }),
  setVoiceState: (voiceState: VoiceState) => set({ voiceState }),
  setAudio: (audio) => set({ audio }),
  setMic: (mic) => set({ mic }),
  setAudioLevel: (audioLevel) => set({ audioLevel }),
}));
