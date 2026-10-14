import { useEffect, useId, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode, type Dispatch, type SetStateAction } from "react";
import { BorderBeam } from "border-beam";
import { Liquid } from "liquid-gooey";
import { ChatCircleText } from "@phosphor-icons/react/dist/csr/ChatCircleText";
import { X } from "@phosphor-icons/react/dist/csr/X";
import { GearSix } from "@phosphor-icons/react/dist/csr/GearSix";
import { MicrophoneSlash } from "@phosphor-icons/react/dist/csr/MicrophoneSlash";
import { Pulse } from "@phosphor-icons/react/dist/csr/Pulse";
import { Play } from "@phosphor-icons/react/dist/csr/Play";
import { Pause } from "@phosphor-icons/react/dist/csr/Pause";
import { SkipBack } from "@phosphor-icons/react/dist/csr/SkipBack";
import { SkipForward } from "@phosphor-icons/react/dist/csr/SkipForward";
import { CaretDown } from "@phosphor-icons/react/dist/csr/CaretDown";
import type { OrbState } from "thinking-orbs";
import { VoiceBeam } from "voice-glow";
import { loadScene, orbStateForEvent, parseRuntimeEvent, pendingTextCommandId, postCommand, postRuntimeCommand, type DashboardWidgets, type ResearchResultData, type RuntimeEvent, type RuntimeSetting, type SceneSnapshot } from "./runtime";
import { readDashboardViewState, writeDashboardViewState, type DashboardViewState } from "./sessionView";
import { SettingsSectionNav, type SettingsSectionId } from "./SettingsSectionNav";
import { ResearchTabs } from "./ResearchTabs";
import { ResearchPanel } from "./ResearchPanel";
import { Microphone } from "@phosphor-icons/react/dist/csr/Microphone";
import { CrispThinkingOrb } from "./CrispThinkingOrb";

type ActivityItem = RuntimeEvent & { key: string };
type CaptionLine = { id: string; text: string; kind: "stt" | "tts" };
type ChatBubble = { id: string; role: "you" | "charlie"; text: string; turnId?: string };
type FrontendSettings = {
  reducedMotion: boolean;
  captions: boolean;
  speechPlayback: boolean;
  activityDensity: "focused" | "expanded";
};

const SETTINGS_KEY = "charlie-frontend-settings";
const DEFAULT_SETTINGS: FrontendSettings = {
  reducedMotion: false,
  captions: true,
  speechPlayback: true,
  activityDensity: "focused",
};

function readFrontendSettings(): FrontendSettings {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "null");
    if (!saved || typeof saved !== "object") return DEFAULT_SETTINGS;
    const value = saved as Partial<FrontendSettings>;
    return {
      reducedMotion: value.reducedMotion === true,
      captions: value.captions !== false,
      speechPlayback: value.speechPlayback !== false,
      activityDensity: value.activityDensity === "expanded" ? "expanded" : "focused",
    };
  } catch { return DEFAULT_SETTINGS; }
}

const ORB_PREVIEW_STATES: Array<{ state: OrbState; label: string }> = [
  { state: "working", label: "Working" },
  { state: "searching", label: "Searching" },
  { state: "solving", label: "Solving" },
  { state: "listening", label: "Listening" },
  { state: "connecting", label: "Connecting" },
  { state: "weaving", label: "Planning / weaving" },
  { state: "composing", label: "Thinking / composing" },
  { state: "breathing", label: "Idle / breathing" },
  { state: "shaping", label: "Shaping" },
];

const ACTIVITY_TYPES = new Set([
  "background_task",
  "task_snapshot",
  "tool_call",
  "tool_result",
  "research_progress",
  "research_result",
  "result_stored",
  "tool_approval_request",
  "tool_approval_resolved",
  "browser_task_started",
  "browser_task_done",
  "alert",
]);
const SESSION_OWNED_EVENT_TYPES = new Set([
  ...[...ACTIVITY_TYPES].filter((type) => type !== "alert"),
  "transcript",
  "token",
]);

export function App() {
  const [settings, setSettings] = useState(readFrontendSettings);
  const previewMode = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("preview") === "orbs";
  const [scene, setScene] = useState<SceneSnapshot | null>(null);
  const [streamOpen, setStreamOpen] = useState(false);
  const [sceneError, setSceneError] = useState("Connecting to Charlie runtime…");
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [commandNotice, setCommandNotice] = useState("");
  const [captionLines, setCaptionLines] = useState<CaptionLine[]>([]);
  const [captionSpeaker, setCaptionSpeaker] = useState<CaptionLine["kind"]>("stt");
  const [reminderNotice, setReminderNotice] = useState("");
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [approvalError, setApprovalError] = useState("");
  const [chatBubbles, setChatBubbles] = useState<ChatBubble[]>([]);
  const [orbState, setOrbState] = useState<OrbState>("connecting");
  const [workPhase, setWorkPhase] = useState<"working" | "solving">("working");
  const [menuOpen, setMenuOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [researchOpen, setResearchOpen] = useState(false);
  const [researchProgress, setResearchProgress] = useState<RuntimeEvent | null>(null);
  const completedResearchTurns = useRef(new Set<string>());
  const [selectedResearchResultId, setSelectedResearchResultId] = useState<string | null>(null);
  const [dismissedResearchResultIds, setDismissedResearchResultIds] = useState<string[]>([]);
  const [viewHydratedSessionId, setViewHydratedSessionId] = useState<string | null>(null);
  const [activeSettingsSection, setActiveSettingsSection] = useState<SettingsSectionId>("presence");
  const micMuted = scene?.voice?.mic_muted ?? true;
  const researchResults = scene?.researchResults?.length
    ? scene.researchResults
    : scene?.researchResult ? [scene.researchResult] : [];
  const selectedResearchResult = selectedResearchResultId
    ? researchResults.find((result) => result.id === selectedResearchResultId)
    : scene?.researchResult;
  const [settingsOpen, setSettingsOpen] = useState(false);
  const clearCaptionTimer = useRef<number | undefined>(undefined);
  const clearReminderTimer = useRef<number | undefined>(undefined);
  const audioLevelRef = useRef(0);
  const ttsBufferRef = useRef("");
  const messageInputRef = useRef<HTMLTextAreaElement>(null);
  const [composerWidth, setComposerWidth] = useState(371);
  const messageEditGenerationRef = useRef(0);
  const replyId = useRef<string | null>(null);
  const presenceRef = useRef<HTMLButtonElement>(null);
  const seenEventIds = useRef(new Set<string>());
  const hydratedSessionRef = useRef<string | null>(null);
  const pendingResearchOpenRef = useRef<string | null>(null);
  const currentSessionIdRef = useRef<string | null>(null);
  const currentSceneRef = useRef<SceneSnapshot | null>(null);
  const sceneRequestGenerationRef = useRef(0);

  async function refreshScene(isActive: () => boolean = () => true): Promise<SceneSnapshot | null> {
    const requestGeneration = ++sceneRequestGenerationRef.current;
    const requestedSessionId = currentSessionIdRef.current;
    const snapshot = await loadScene();
    const activeSessionId = currentSessionIdRef.current;
    const acceptedScene = currentSceneRef.current;
    if (!isActive() || requestGeneration !== sceneRequestGenerationRef.current) return acceptedScene;
    if (activeSessionId !== requestedSessionId && activeSessionId !== null) return acceptedScene;
    if (requestedSessionId === null && activeSessionId !== null && snapshot.sessionId !== activeSessionId) return acceptedScene;
    currentSessionIdRef.current = snapshot.sessionId ?? null;
    currentSceneRef.current = snapshot;
    setScene(snapshot);
    return snapshot;
  }

  useEffect(() => {
    if (!scene?.sessionId || viewHydratedSessionId === scene.sessionId) return;
    const saved = readDashboardViewState(scene.sessionId);
    const pendingResearchId = pendingResearchOpenRef.current;
    const restored = pendingResearchId
      ? {
        ...saved,
        researchOpen: true,
        selectedResearchResultId: pendingResearchId,
        dismissedResearchResultIds: saved.dismissedResearchResultIds.filter((id) => id !== pendingResearchId),
      }
      : saved;
    hydratedSessionRef.current = scene.sessionId;
    pendingResearchOpenRef.current = null;
    setChatOpen(restored.chatOpen);
    setDrawerOpen(restored.drawerOpen);
    setSettingsOpen(restored.settingsOpen);
    setActiveSettingsSection(restored.activeSettingsSection);
    setResearchOpen(restored.researchOpen);
    setSelectedResearchResultId(restored.selectedResearchResultId);
    setDismissedResearchResultIds(restored.dismissedResearchResultIds);
    setChatBubbles(scene.conversationHistory ?? []);
    completedResearchTurns.current = new Set((scene.activity ?? [])
      .filter((event) => event.type === "research_result" && event.turnId)
      .map((event) => event.turnId!));
    setResearchProgress(scene.activeTurnId
      ? [...(scene.activity ?? [])].reverse().find((event) => event.type === "research_progress" && event.turnId === scene.activeTurnId) ?? null
      : null);
    setActivity((current) => {
      const seen = new Set(current.map((item) => item.key));
      const restored = [...(scene.activity ?? [])].reverse().flatMap((item, index) => {
        const key = item.id ?? `${item.type}-${item.timestamp ?? index}`;
        return seen.has(key) ? [] : [{ ...item, key }];
      });
      return [...current, ...restored].slice(0, 40);
    });
    setViewHydratedSessionId(scene.sessionId);
  }, [scene?.sessionId, viewHydratedSessionId]);

  useEffect(() => {
    if (!scene?.sessionId || viewHydratedSessionId !== scene.sessionId) return;
    const state: DashboardViewState = {
      chatOpen,
      drawerOpen,
      settingsOpen,
      activeSettingsSection,
      researchOpen,
      selectedResearchResultId,
      dismissedResearchResultIds,
    };
    writeDashboardViewState(scene.sessionId, state);
  }, [scene?.sessionId, viewHydratedSessionId, chatOpen, drawerOpen, settingsOpen, activeSettingsSection, researchOpen, selectedResearchResultId, dismissedResearchResultIds]);

  useEffect(() => {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch { /* Preferences are optional. */ }
  }, [settings]);

  function addChatBubble(role: ChatBubble["role"], text: string, id: string = crypto.randomUUID(), turnId?: string) {
    const clean = text.trim();
    if (!clean) return;
    setChatBubbles((bubbles) => bubbles.some((bubble) => bubble.id === id) ? bubbles : [...bubbles, { id, role, text: clean, ...(turnId !== undefined ? { turnId } : {}) }]);
  }

  function appendCharlieBubble(text: string, turnId?: string) {
    if (!text) return;
    const id = turnId ? `turn:${turnId}` : replyId.current ?? crypto.randomUUID();
    replyId.current = id;
    setChatBubbles((bubbles) => {
      if (turnId && bubbles.some((bubble) => bubble.role === "charlie" && bubble.turnId === turnId && bubble.id.startsWith("research:"))) return bubbles;
      const next = [...bubbles];
      const index = next.findIndex((bubble) => bubble.id === id);
      if (index >= 0) next[index] = { ...next[index], ...(turnId !== undefined ? { turnId } : {}), text: `${next[index].text}${text}` };
      else {
        next.push({ id, role: "charlie", text, ...(turnId !== undefined ? { turnId } : {}) });
      }
      return next;
    });
  }

  function openResearchResult(id: string) {
    if (!hydratedSessionRef.current) pendingResearchOpenRef.current = id;
    setSelectedResearchResultId(id);
    setResearchOpen(true);
    setDismissedResearchResultIds((ids) => ids.filter((item) => item !== id));
  }

  function dismissResearchResult(id: string) {
    setDismissedResearchResultIds((ids) => ids.includes(id) ? ids : [...ids, id].slice(-10));
    if (selectedResearchResultId === id) setResearchOpen(false);
  }

  function queueCaptionClear(delay: number) {
    if (clearCaptionTimer.current !== undefined) window.clearTimeout(clearCaptionTimer.current);
    clearCaptionTimer.current = window.setTimeout(() => setCaptionLines([]), delay);
  }

  function addCaption(text: string, kind: CaptionLine["kind"], partial = false) {
    const clean = text.trim();
    if (!clean) return;
    setCaptionSpeaker(kind);
    setCaptionLines((lines) => [...lines.filter((line) => line.kind !== kind), { id: kind, text: clean, kind }]);
    if (clearCaptionTimer.current !== undefined) window.clearTimeout(clearCaptionTimer.current);
    if (!partial) queueCaptionClear(Math.max(12000, clean.length * 40));
  }

  function appendTtsCaption(text: string) {
    addCaption(text, "tts");
  }

  useEffect(() => {
    if (previewMode) return;
    let active = true;
    const stream = new EventSource("/api/events");
    stream.onopen = () => {
      setStreamOpen(true);
      void refreshScene(() => active).then((snapshot) => {
        if (!active || !snapshot || snapshot.sessionId !== currentSessionIdRef.current) return;
        setActivity((items) => items.filter((item) => item.type !== "tool_approval_request"
          || item.requestId === snapshot.pendingApproval?.requestId));
        setSceneError("");
        setOrbState((current) => current === "connecting" ? "breathing" : current);
      }).catch((error: unknown) => {
        if (active) setSceneError(error instanceof Error ? error.message : "Scene endpoint is unavailable.");
      });
    };
    stream.onerror = () => {
      if (!active) return;
      setStreamOpen(false);
      setOrbState("connecting");
    };
    stream.onmessage = (messageEvent) => {
      let raw: unknown;
      try { raw = JSON.parse(messageEvent.data); } catch { return; }
      const event = parseRuntimeEvent(raw);
      if (!event) return;
      const sessionOwned = SESSION_OWNED_EVENT_TYPES.has(event.type);
      const projectSessionEvent = !sessionOwned || Boolean(currentSessionIdRef.current
        && event.sessionId === currentSessionIdRef.current);
      if (event.id) {
        if (seenEventIds.current.has(event.id)) return;
        seenEventIds.current.add(event.id);
        if (seenEventIds.current.size > 512) {
          seenEventIds.current.delete(seenEventIds.current.values().next().value as string);
        }
      }
      if (event.type === "alert" && event.channel === "web" && event.text?.startsWith("Reminder:")) {
        const reminder = event.text.slice("Reminder:".length).trim();
        setReminderNotice(reminder === "Timer finished" ? reminder : `Reminder: ${reminder}`);
        if (clearReminderTimer.current !== undefined) window.clearTimeout(clearReminderTimer.current);
        clearReminderTimer.current = window.setTimeout(() => setReminderNotice(""), 8000);
      }
      if (event.type === "audio_level" && event.level !== undefined) audioLevelRef.current = event.level;
      if (["speaking_stop", "response_done"].includes(event.type)) audioLevelRef.current = 0;
      const nextOrbState = projectSessionEvent ? orbStateForEvent(event) : null;
      if (nextOrbState) setOrbState(nextOrbState);
      if (event.snapshot?.sessionId === currentSessionIdRef.current) {
        ++sceneRequestGenerationRef.current;
        currentSceneRef.current = event.snapshot;
        setScene(event.snapshot);
      }
      if (event.type === "dashboard_widgets" && event.channel === "web" && event.widgets) {
        setScene((current) => current && event.sessionId === current.sessionId
          ? { ...current, widgets: event.widgets }
          : current);
      }
      if (event.type === "system_status" && event.systemStatus) {
        setScene((current) => current?.widgets && !current.widgets.hiddenWidgets?.includes("system_status")
          ? { ...current, widgets: { ...current.widgets, systemStatus: event.systemStatus } }
          : current);
      }
      if (projectSessionEvent && event.type === "transcript" && event.text) {
        if (!event.partial) addChatBubble("you", event.text, event.turnId ? `turn:${event.turnId}:you` : undefined, event.turnId);
        addCaption(event.text, "stt", event.partial);
      }
      if (["vad_start", "ptt_start"].includes(event.type)) {
        setCaptionSpeaker("stt");
        if (clearCaptionTimer.current !== undefined) window.clearTimeout(clearCaptionTimer.current);
      }
      if (projectSessionEvent && event.type === "token" && event.channel !== "telegram" && event.text) {
        ttsBufferRef.current += event.text;
        appendCharlieBubble(event.text, event.turnId);
      }
      if (projectSessionEvent && event.type === "research_result" && event.channel !== "telegram") {
        if (event.researchResult) {
          if (event.turnId) completedResearchTurns.current.add(event.turnId);
          setResearchProgress((current) => !current?.turnId || !event.turnId || current.turnId === event.turnId ? null : current);
          const result = event.researchResult;
          setChatBubbles((bubbles) => {
            const inlineBubble: ChatBubble = {
              id: `research:${result.id}`,
              role: "charlie",
              text: result.answer || result.text,
              ...(event.turnId !== undefined ? { turnId: event.turnId } : {}),
            };
            const existing = bubbles.findIndex((bubble) => bubble.id === inlineBubble.id
              || (event.turnId && bubble.role === "charlie" && bubble.turnId === event.turnId && !bubble.id.startsWith("research:")));
            return existing < 0 ? [...bubbles, inlineBubble] : bubbles.map((bubble, index) => index === existing ? inlineBubble : bubble);
          });
          setScene((cur) => {
            if (!cur) return cur;
            const previous = cur.researchResults ?? (cur.researchResult ? [cur.researchResult] : []);
            const next = [...previous.filter((item) => item.id !== result.id), result].slice(-10);
            return { ...cur, researchResult: result, researchResults: next };
          });
          openResearchResult(result.id);
        }
      }
      if (projectSessionEvent && event.type === "research_progress" && event.text && event.channel !== "telegram"
        && (!event.turnId || !completedResearchTurns.current.has(event.turnId))) {
        setResearchProgress(event);
      }
      if (projectSessionEvent && event.type === "result_stored" && event.channel === "web" && event.text) {
        replyId.current = null;
        addChatBubble("charlie", event.text, `task:${event.resultId ?? event.id}`);
      }
      if (["mic_state", "audio_state", "settings_snapshot"].includes(event.type)) {
        void refreshScene().catch((error: unknown) => setSceneError(error instanceof Error ? error.message : "Scene endpoint is unavailable."));
      }
      if (["task_snapshot", "background_task", "research_result", "result_stored"].includes(event.type)) {
        void refreshScene().catch((error: unknown) => setSceneError(error instanceof Error ? error.message : "Scene endpoint is unavailable."));
      }
      if (projectSessionEvent && event.type === "tool_approval_resolved" && event.requestId) {
        setActivity((items) => items.filter((item) => item.requestId !== event.requestId));
        setScene((current) => current && current.pendingApproval?.requestId === event.requestId
          ? { ...current, pendingApproval: undefined } : current);
      }
      if (projectSessionEvent && event.type === "tool_approval_request" && event.channel === "web") {
        setApprovalError("");
        setScene((current) => current ? { ...current, pendingApproval: event } : current);
      }
      if (event.type === "speaking_start") {
        const spoken = event.text || ttsBufferRef.current.trim();
        if (spoken) appendTtsCaption(spoken);
        if (clearCaptionTimer.current !== undefined) window.clearTimeout(clearCaptionTimer.current);
        ttsBufferRef.current = "";
      }
      if (event.type === "speaking_stop") queueCaptionClear(12000);
      if (event.type === "response_done") {
        replyId.current = null;
        ttsBufferRef.current = "";
      }
      if (projectSessionEvent && ACTIVITY_TYPES.has(event.type)) {
        setActivity((items) => [{ ...event, key: event.id ?? `${event.type}-${Date.now()}` }, ...items].slice(0, 40));
      }
    };
    void refreshScene(() => active).then((snapshot) => {
      if (!active || !snapshot) return;
      setSceneError("");
    }).catch((error: unknown) => {
      if (active) setSceneError(error instanceof Error ? error.message : "Scene endpoint is unavailable.");
    });
    return () => {
      active = false;
      stream.close();
      if (clearCaptionTimer.current !== undefined) window.clearTimeout(clearCaptionTimer.current);
      if (clearReminderTimer.current !== undefined) window.clearTimeout(clearReminderTimer.current);
    };
  }, [previewMode]);

  useEffect(() => {
    const working = sending || scene?.conversationState === "working" || Boolean(scene?.activeTurnId) || (scene?.tasks ?? []).some((task) => !["completed", "failed", "cancelled"].includes(task.status));
    if (!working) {
      setWorkPhase("working");
      return;
    }
    const interval = window.setInterval(() => setWorkPhase((phase) => phase === "working" ? "solving" : "working"), 1800);
    return () => window.clearInterval(interval);
  }, [sending, scene?.conversationState, scene?.activeTurnId, scene?.tasks]);

  useEffect(() => {
    if (chatOpen) messageInputRef.current?.focus();
  }, [chatOpen]);

  useEffect(() => {
    const input = messageInputRef.current;
    if (!input) return;
    const maxWidth = Math.max(280, Math.min(520, window.innerWidth - 32));
    const minWidth = Math.min(371, maxWidth);
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (context) {
      context.font = getComputedStyle(input).font;
      const longestLine = Math.max(...message.split(/\r?\n/).map((line) => context.measureText(line || " ").width));
      const nextWidth = Math.min(maxWidth, Math.max(minWidth, Math.ceil(longestLine + 126)));
      setComposerWidth((current) => current === nextWidth ? current : nextWidth);
    }
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
    input.style.overflowY = input.scrollHeight > 120 ? "auto" : "hidden";
  }, [message, chatOpen, composerWidth]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      setChatOpen(false);
      setSettingsOpen(false);
      setDrawerOpen(false);
      presenceRef.current?.focus();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = message.trim();
    if (!text || sending || !scene || !streamOpen) return;
    setSending(true);
    setCommandNotice("");
    const sessionId = scene.sessionId;
    if (!sessionId) {
      setCommandNotice("The active Charlie session is unavailable.");
      setSending(false);
      return;
    }
    const messageEditGeneration = messageEditGenerationRef.current;
    replyId.current = null;
    setMessage("");
    let requestId: string | null = null;
    try {
      requestId = pendingTextCommandId(text, sessionId);
      addChatBubble("you", text, `submit:${requestId}`);
      const accepted = await postCommand(text, fetch, sessionId);
      if (!accepted) {
        setCommandNotice((current) => currentSessionIdRef.current === sessionId
          ? "Response received; command acceptance was not confirmed." : current);
        setMessage((current) => currentSessionIdRef.current === sessionId
          && messageEditGenerationRef.current === messageEditGeneration && current === "" ? text : current);
      }
    } catch (error) {
      const failure = error instanceof Error ? error.message : "Command could not be sent.";
      setCommandNotice((current) => currentSessionIdRef.current === sessionId ? failure : current);
      const failedRequestId = requestId;
      if (failedRequestId) setChatBubbles((bubbles) => currentSessionIdRef.current !== sessionId
        || bubbles.some((bubble) => bubble.id === `submit-error:${failedRequestId}`)
        ? bubbles : [...bubbles, { id: `submit-error:${failedRequestId}`, role: "charlie", text: failure }]);
      setMessage((current) => currentSessionIdRef.current === sessionId
        && messageEditGenerationRef.current === messageEditGeneration && current === "" ? text : current);
    } finally {
      setSending(false);
    }
  }

  function toggleMenu() {
    setMenuOpen((open) => !open);
    setSettingsOpen(false);
  }

  function openChat() {
    setMenuOpen(false);
    setSettingsOpen(false);
    setChatOpen(true);
  }

  async function sendRuntimeControl(command: Record<string, unknown>) {
    try {
      if (!await postRuntimeCommand(command)) throw new Error("Runtime control was not acknowledged.");
      await refreshScene();
      setCommandNotice("");
    } catch (error) {
      setCommandNotice(error instanceof Error ? error.message : "Runtime control failed.");
    }
  }

  function setVoiceState(type: "set_mic_state" | "set_audio_state", muted: boolean) {
    return sendRuntimeControl({ type, [type === "set_mic_state" ? "mic_muted" : "muted"]: muted });
  }

  async function decideApproval(approval: RuntimeEvent, approved: boolean) {
    if (approval.channel !== "web" || !approval.requestId) return;
    const requestId = approval.requestId;
    setApprovalBusy(true);
    setApprovalError("");
    try {
      if (!await postRuntimeCommand({ type: approved ? "approve" : "reject", request_id: requestId })) {
        throw new Error("Charlie did not acknowledge the decision.");
      }
      setActivity((items) => items.filter((item) => item.requestId !== requestId));
      setScene((current) => current && current.pendingApproval?.requestId === requestId
        ? { ...current, pendingApproval: undefined } : current);
    } catch (error) {
      setApprovalError(error instanceof Error ? error.message : "Could not send the decision.");
    } finally {
      setApprovalBusy(false);
    }
  }

  async function saveRuntimeSettings(updates: Record<string, unknown>) {
    if (!await postRuntimeCommand({ type: "update_settings", updates })) throw new Error("Settings save was not acknowledged.");
    const updated = await refreshScene();
    return updated?.settings ?? [];
  }

  async function setDashboardWidgetVisibility(widget: "clock" | "weather" | "timer" | "stopwatch" | "music" | "system_status", visible: boolean, widgetId?: string) {
    try {
      if (!await postRuntimeCommand({ type: "set_dashboard_widget_visibility", widget, visible, ...(widgetId ? { widget_id: widgetId } : {}) })) {
        throw new Error(`Charlie did not confirm the ${widget} widget change.`);
      }
      await refreshScene();
      setCommandNotice("");
    } catch (error) {
      setCommandNotice(error instanceof Error ? error.message : `The ${widget} widget could not be updated.`);
      throw error;
    }
  }

  async function updateDashboardTimer(scheduleId: string, text: string, delaySeconds: number, deliveryChannel: "web" | "voice") {
    if (!await postRuntimeCommand({ type: "update_timer", schedule_id: scheduleId, text, delay_seconds: delaySeconds, delivery_channel: deliveryChannel })) {
      throw new Error("Charlie did not confirm the timer change.");
    }
    await refreshScene();
  }

  async function controlDashboardStopwatch(action: "start" | "stop" | "reset") {
    if (!await postRuntimeCommand({ type: "stopwatch_control", action })) {
      throw new Error("Charlie did not confirm the stopwatch change.");
    }
    await refreshScene();
  }

  async function controlDashboardMedia(action: "previous track" | "play/pause" | "next track") {
    const mediaAction = action === "previous track" ? "prev_track" : action === "next track" ? "next_track" : "play_pause";
    if (!await postRuntimeCommand({ type: "media_control", action: mediaAction })) {
      throw new Error("Charlie could not confirm the media command.");
    }
    await refreshScene();
  }

  function toggleMute() {
    setMenuOpen(false);
    setSettingsOpen(false);
    void setVoiceState("set_mic_state", !micMuted);
    presenceRef.current?.focus();
  }

  function openActivity() {
    setMenuOpen(false);
    setSettingsOpen(false);
    setChatOpen(false);
    setDrawerOpen(true);
  }

  function closeActivity() {
    setDrawerOpen(false);
    presenceRef.current?.focus();
  }

  function openSettings() {
    setMenuOpen(false);
    setChatOpen(false);
    setSettingsOpen(true);
  }

  if (previewMode) return <PageBeam><OrbPreview /></PageBeam>;

  const connected = Boolean(scene && streamOpen);
  const activeTasks = (scene?.tasks ?? []).filter((task) => !["completed", "failed", "cancelled"].includes(task.status)).slice(0, 24);
  const cancellableTaskIds = new Set(activeTasks.map((task) => task.id));
  const runtimeWorking = sending || scene?.conversationState === "working" || Boolean(scene?.activeTurnId) || activeTasks.length > 0;
  const visibleOrbState = !connected ? "connecting" : runtimeWorking
    ? (["searching", "shaping", "weaving", "composing", "listening"].includes(orbState) ? orbState : workPhase)
    : orbState;
  const focusedActivity = activity.filter((item) => !["tool_call", "tool_result"].includes(item.type));
  const visibleActivity = settings.activityDensity === "expanded" || focusedActivity.length === 0 ? activity : focusedActivity;
  const visibleResearchResults = researchResults.filter((result) => !dismissedResearchResultIds.includes(result.id));
  const researchTabsHeight = Math.min(360, Math.max(40, visibleResearchResults.length * 48 - 8));
  const researchProgressSteps = researchProgress?.turnId
    ? activity.filter((item) => item.type === "research_progress" && item.turnId === researchProgress.turnId).slice(0, 5).reverse()
    : [];
  const pendingApproval = activity.find((item) => item.type === "tool_approval_request"
    && item.channel === "web" && item.requestId) ?? scene?.pendingApproval;
  return (
    <PageBeam>
      <main className={`workspace ${chatOpen ? "is-chat-open" : ""} ${drawerOpen ? "is-drawer-open" : ""} ${!chatOpen && researchOpen && selectedResearchResult ? "has-research" : ""} ${settings.reducedMotion ? "is-reduced-motion" : ""}`} style={{ "--research-tabs-height": `${researchTabsHeight}px` } as CSSProperties}>
      {!connected && <div className="runtime-notice" role="status">Reconnecting to Charlie. Previous messages are historical.</div>}
      {!chatOpen && commandNotice && <div className="runtime-notice" role="status">{commandNotice}</div>}
      {reminderNotice && <div className="reminder-toast" role="status" aria-live="polite">{reminderNotice}</div>}
      <header className="topbar">
        <a className="wordmark" href="#home" aria-label="Charlie home">CHARLIE</a>
        <div className="connection" role="status" aria-live="polite">
          <span className={`connection-mark ${connected ? "is-connected" : ""}`} />
          {connected ? "Connected" : scene && !streamOpen ? "Live updates unavailable" : "Disconnected"}
        </div>
        <button className="activity-toggle" type="button" aria-expanded={drawerOpen} aria-controls="activity-panel" onClick={() => drawerOpen ? closeActivity() : setDrawerOpen(true)}>
          Activity <span className="activity-count">{activity.length}</span>
        </button>
      </header>

      <section className="stage" aria-label="Charlie visual stage">
        <Liquid className="orb-menu" fill="#202020" blur={6} contrast={18} shadow="0 4px 18px rgba(0,0,0,.16)">
          {menuOpen && <Liquid.Item style={{ position: "absolute", left: 238, top: 198 }} x={-170} y={-160} transition="bouncy" delay={0}>
            <button className="orb-action is-visible" type="button" aria-label="Chat" title="Chat" onClick={openChat}><ChatCircleText size={20} weight="regular" aria-hidden="true" /></button>
          </Liquid.Item>}
          {menuOpen && <Liquid.Item style={{ position: "absolute", left: 238, top: 198 }} x={170} y={-160} transition="bouncy" delay={40}>
            <button className="orb-action orb-action--right is-visible" type="button" aria-label={micMuted ? "Unmute microphone" : "Mute microphone"} title={micMuted ? "Unmute microphone" : "Mute microphone"} disabled={!scene?.voice?.enabled} onClick={toggleMute}>{micMuted ? <MicrophoneSlash size={20} weight="regular" aria-hidden="true" /> : <Microphone size={20} weight="regular" aria-hidden="true" />}</button>
          </Liquid.Item>}
          {menuOpen && <Liquid.Item style={{ position: "absolute", left: 238, top: 198 }} x={-170} y={160} transition="bouncy" delay={80}>
            <button className="orb-action is-visible" type="button" aria-label="Activity" title="Activity" onClick={openActivity}><Pulse size={20} weight="regular" aria-hidden="true" /></button>
          </Liquid.Item>}
          {menuOpen && <Liquid.Item style={{ position: "absolute", left: 238, top: 198 }} x={170} y={160} transition="bouncy" delay={120}>
            <button className="orb-action orb-action--right is-visible" type="button" aria-label="Settings" title="Settings" onClick={openSettings}><GearSix size={20} weight="regular" aria-hidden="true" /></button>
          </Liquid.Item>}
          <Liquid.Item style={{ position: "absolute", left: 132, top: 92 }}>
            <button ref={presenceRef} className="presence" type="button" aria-expanded={menuOpen} aria-label={menuOpen ? "Close Charlie options" : "Open Charlie options"} onClick={toggleMenu}>
              <CrispThinkingOrb state={visibleOrbState} speed={0.9} />
            </button>
          </Liquid.Item>
        </Liquid>
        {settings.captions && <LiveCaption lines={captionLines} speaker={captionSpeaker} />}
        {scene ? <article className="scene-content" aria-live="polite">
          <h1>{scene.title}</h1>
          {scene.summary && <p className="scene-summary">{scene.summary}</p>}
          {scene.details.length > 0 && <dl className="scene-details">{scene.details.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>}
        </article> : <p className="empty-state">{sceneError}</p>}
      </section>

      <DashboardWidgetsPanel widgets={scene?.widgets} sessionId={scene?.sessionId ?? ""} chatOpen={chatOpen} researching={Boolean(researchProgress?.text)} onWidgetVisibilityChange={setDashboardWidgetVisibility} onUpdateTimer={updateDashboardTimer} onStopwatchAction={controlDashboardStopwatch} onMediaCommand={controlDashboardMedia} />

      {settingsOpen && <SettingsPanel settings={settings} setSettings={setSettings} activeSection={activeSettingsSection} onSelectSection={setActiveSettingsSection} voice={scene?.voice} runtimeSettings={scene?.settings} onSaveSettings={saveRuntimeSettings} onVoiceState={setVoiceState} onClose={() => { setSettingsOpen(false); presenceRef.current?.focus(); }} />}

      {chatOpen && <div className="voice-composer-shell" style={{ "--composer-width": `${composerWidth}px` } as CSSProperties}>
        <ChatBubbles bubbles={chatBubbles} researchResults={researchResults} />
        <VoiceBeam className="voice-composer-beam" type="default" colorVariant="mono" theme="dark" scale={1} level={() => micMuted ? 0 : audioLevelRef.current} processing={runtimeWorking || (!micMuted && visibleOrbState === "working")} strength={0.72} idle={0.42} processingLevel={0.45}>
          <form className="composer composer--open" onSubmit={submit} aria-label="Send a message to Charlie">
            <button className="composer-close" type="button" aria-label="Close Chat" onClick={() => { setChatOpen(false); presenceRef.current?.focus(); }}>×</button>
            <label className="sr-only" htmlFor="message">Message Charlie</label>
            <textarea ref={messageInputRef} id="message" value={message} rows={1} onChange={(event) => { messageEditGenerationRef.current += 1; setMessage(event.target.value); }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="Talk to Charlie…" autoComplete="off" />
            <button type="submit" disabled={!message.trim() || sending || !connected}>{sending ? "Sending" : "Send"}</button>
            <p className="command-notice sr-only" aria-live="polite">{commandNotice}</p>
          </form>
        </VoiceBeam>
      </div>}

      {researchProgress?.text && <ResearchProgressWidget progress={researchProgress} steps={researchProgressSteps.length ? researchProgressSteps : [researchProgress]} connected={connected} />}
      {!chatOpen && visibleResearchResults.length > 0 && (
        <ResearchTabs
          results={visibleResearchResults}
          selectedId={selectedResearchResultId}
          onSelect={openResearchResult}
          onDismiss={dismissResearchResult}
        />
      )}

      {!chatOpen && researchOpen && selectedResearchResult && (
        <ResearchPanel
          result={selectedResearchResult}
          onClose={() => setResearchOpen(false)}
        />
      )}

      {pendingApproval?.requestId && <ApprovalPopup
        title={pendingApproval.operationPreview || "Allow this action?"}
        reason={pendingApproval.summary || "Charlie needs your approval before continuing."}
        busy={approvalBusy || !connected} error={approvalError}
        onDecision={(approved) => void decideApproval(pendingApproval, approved)}
      />}

      <aside id="activity-panel" className={`activity-panel ${drawerOpen ? "is-open" : ""}`} aria-label="Activity" aria-hidden={!drawerOpen}>
        <div className="drawer-heading"><h2>Activity</h2><button type="button" aria-label="Close activity" onClick={closeActivity}>Close</button></div>
        {activeTasks.length || visibleActivity.length ? <ol className="activity-list">
          {activeTasks.map((task) => <li key={`task-${task.id}`}><strong>{task.title}</strong><span>{task.currentAction || task.status}</span><button type="button" onClick={() => void sendRuntimeControl({ type: "cancel_task", task_id: task.id })}>Cancel task</button>{task.updatedAt && <time dateTime={task.updatedAt}>{task.updatedAt}</time>}</li>)}
          {visibleActivity.map((item) => <li key={item.key}><strong>{activityLabel(item)}</strong>{item.summary && <span>{item.summary}</span>}{item.type === "tool_approval_request" && item.channel === "web" && item.requestId && <div><button type="button" disabled={approvalBusy || !connected} onClick={() => void decideApproval(item, true)}>Approve</button><button type="button" disabled={approvalBusy || !connected} onClick={() => void decideApproval(item, false)}>Decline</button></div>}{item.taskId && cancellableTaskIds.has(item.taskId) && <div><button type="button" disabled={!connected} onClick={() => void sendRuntimeControl({ type: "cancel_task", task_id: item.taskId })}>Cancel task</button></div>}{item.timestamp && <time dateTime={item.timestamp}>{item.timestamp}</time>}</li>)}
        </ol> : <p className="activity-empty">No activity in this Charlie session yet.</p>}
      </aside>
      {drawerOpen && <button className="drawer-scrim" type="button" aria-label="Close activity" onClick={closeActivity} />}
      </main>
    </PageBeam>
  );
}

function PageBeam({ children }: { children: ReactNode }) {
  return <BorderBeam className="page-beam" size="pulse-inner" colorVariant="mono" theme="dark" active strength={0.9}>{children}</BorderBeam>;
}

function activityLabel(item: ActivityItem): string {
  switch (item.type) {
    case "tool_call": return "Working";
    case "tool_result": return "Work completed";
    case "research_progress": return "Researching";
    case "research_result":
    case "result_stored": return "Result ready";
    case "background_task":
    case "task_snapshot": return "Background work";
    case "tool_approval_request": return "Approval needed";
    case "tool_approval_resolved": return "Approval resolved";
    case "browser_task_started": return "Browser work";
    case "browser_task_done": return "Browser result ready";
    case "alert": return "Attention";
    default: return "Charlie work";
  }
}

function SettingsPanel({ onClose, voice, onVoiceState, settings, setSettings, runtimeSettings, onSaveSettings, activeSection, onSelectSection }: { onClose: () => void; voice: SceneSnapshot["voice"]; onVoiceState: (type: "set_mic_state" | "set_audio_state", muted: boolean) => Promise<void>; settings: FrontendSettings; setSettings: Dispatch<SetStateAction<FrontendSettings>>; runtimeSettings: RuntimeSetting[] | undefined; onSaveSettings: (updates: Record<string, unknown>) => Promise<RuntimeSetting[]>; activeSection: SettingsSectionId; onSelectSection: (section: SettingsSectionId) => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => dialog?.close();
  }, []);
  function close() {
    dialogRef.current?.close();
    onClose();
  }

  function setSetting<K extends keyof FrontendSettings>(key: K, value: FrontendSettings[K]) {
    setSettings((current) => ({ ...current, [key]: value }));
  }

  return (
    <dialog ref={dialogRef} className="settings-panel" aria-label="Charlie settings" onCancel={(event) => { event.preventDefault(); close(); }}>
      <div className="settings-panel__header">
        <div>
          <h2>Settings</h2>
          <p>Runtime settings are saved to your .env file. Restart-required changes stay marked.</p>
        </div>
        <button type="button" aria-label="Close Settings" onClick={close}>Close</button>
      </div>
      <SettingsSectionNav active={activeSection} onSelect={onSelectSection} />
      <div className="settings-panel__body">
        <SettingsSection id="settings-presence" title="Presence" hidden={activeSection !== "presence"}>
          <SettingToggle label="Reduced motion" description="Keep the orb and panel transitions still." checked={settings.reducedMotion} onChange={(value) => setSetting("reducedMotion", value)} />
          <SettingToggle label="Live captions" description="Show short transcript and response captions on the stage." checked={settings.captions} onChange={(value) => setSetting("captions", value)} />
          <SettingSelect label="Activity detail" description="Choose how much runtime work appears in the Activity drawer." value={settings.activityDensity} onChange={(value) => setSetting("activityDensity", value as FrontendSettings["activityDensity"])} options={[{ value: "focused", label: "Focused" }, { value: "expanded", label: "Expanded" }]} />
        </SettingsSection>

        <SettingsSection id="settings-voice" title="Speech and listening" hidden={activeSection !== "voice"}>
          {voice?.enabled ? <>
            <SettingToggle label="Microphone" description="Control Charlie's native microphone input." checked={!voice.mic_muted} onChange={(value) => void onVoiceState("set_mic_state", !value)} />
            <SettingToggle label="Speech playback" description="Control Charlie's native speaker playback." checked={!voice.muted} onChange={(value) => void onVoiceState("set_audio_state", !value)} />
          </> : <p role="status">Native voice is unavailable.</p>}
        </SettingsSection>

        <RuntimeSettingsEditor id="settings-runtime" hidden={activeSection !== "runtime"} fields={runtimeSettings} onSave={onSaveSettings} />

        <SettingsSection id="settings-privacy" title="Session history" hidden={activeSection !== "privacy"}>
          <p>Conversation, research, and activity history are kept with the active Charlie session and restored when this page reloads.</p>
        </SettingsSection>

        <p className="settings-footnote">API keys remain hidden.</p>
      </div>
    </dialog>
  );
}

function RuntimeSettingsEditor({ id, hidden = false, fields, onSave }: { id: string; hidden?: boolean; fields: RuntimeSetting[] | undefined; onSave: (updates: Record<string, unknown>) => Promise<RuntimeSetting[]> }) {
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState<Record<string, string | boolean>>({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const obsolete = new Set(["EXA_API_KEY", "TAVILY_API_KEY", "RESEARCH_CRAWL_ENABLED", "RESEARCH_CRAWL_MAX_DEPTH", "RESEARCH_CRAWL_MAX_PAGES"]);
  const editable = (fields ?? []).filter((field) => !obsolete.has(field.key));
  const shown = editable.filter((field) => `${field.label} ${field.key} ${field.group}`.toLowerCase().includes(search.toLowerCase()));
  const groups = [...new Set(shown.map((field) => field.group))];
  const displayValue = (field: RuntimeSetting): string | boolean => field.secret ? "" : field.type === "bool" ? field.value === true : Array.isArray(field.value) ? field.value.join(", ") : String(field.value ?? "");
  const changes = Object.fromEntries(editable.flatMap((field) => {
    const value = draft[field.key];
    return value === undefined || value === displayValue(field) || (field.secret && value === "") ? [] : [[field.key, value]];
  }));
  async function save() {
    setSaving(true);
    setNotice("");
    try {
      const saved = await onSave(changes);
      setDraft({});
      setNotice(saved.some((field) => field.pending) ? "Saved to .env. Restart Charlie to apply pending changes." : "Saved and applied.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Settings could not be saved.");
    } finally { setSaving(false); }
  }
  return <section id={id} className="runtime-settings" aria-label="Runtime configuration" hidden={hidden}>
    <h3>Runtime configuration</h3>
    {fields ? <>
      <label className="runtime-settings-search">Find a setting<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Model, voice, research…" /></label>
      {groups.map((group) => <details className="runtime-settings-group" key={group} open={search.trim() ? true : undefined}>
        <summary>{group}<span>{shown.filter((field) => field.group === group).length}</span></summary>
        {shown.filter((field) => field.group === group).map((field) => field.type === "bool"
          ? <SettingToggle
            key={field.key}
            label={field.label}
            description={`${field.key}${field.pending ? " · Restart pending" : ""}`}
            checked={(draft[field.key] ?? displayValue(field)) === true}
            disabled={saving}
            onChange={(value) => setDraft((current) => ({ ...current, [field.key]: value }))}
          />
          : <label className="runtime-setting" key={field.key}>
            <span><strong>{field.label}</strong><small>{field.key}{field.pending ? " · Restart pending" : ""}</small></span>
            <input type={field.secret ? "password" : field.type === "int" || field.type === "float" ? "number" : "text"} step={field.type === "float" ? "any" : "1"} autoComplete="off" value={String(draft[field.key] ?? displayValue(field))} placeholder={field.secret ? field.isSet ? "Configured — enter to replace" : "Not configured" : undefined} disabled={saving} onChange={(event) => setDraft((current) => ({ ...current, [field.key]: event.target.value }))} />
          </label>)}
      </details>)}
      {!groups.length && <p>No matching settings.</p>}
      <div className="runtime-settings-save"><button type="button" disabled={saving || !Object.keys(changes).length} onClick={() => void save()}>{saving ? "Saving…" : "Save runtime settings"}</button><button type="button" disabled={saving || !Object.keys(changes).length} onClick={() => { setDraft({}); setNotice(""); }}>Discard edits</button></div>
      <p role="status">{notice}</p>
    </> : <p role="status">Runtime settings are unavailable while disconnected.</p>}
  </section>;
}

function SettingsSection({ id, title, hidden = false, children }: { id: string; title: string; hidden?: boolean; children: ReactNode }) {
  return <section id={id} className="settings-section" hidden={hidden}><h3>{title}</h3><div>{children}</div></section>;
}

function SettingToggle({ label, description, checked, disabled = false, onChange }: { label: string; description: string; checked: boolean; disabled?: boolean; onChange: (value: boolean) => void }) {
  return <label className="settings-toggle"><span><strong>{label}</strong><small>{description}</small></span><input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} /><span className="settings-switch" aria-hidden="true" /></label>;
}

function SettingSelect({ label, description, value, onChange, options }: { label: string; description: string; value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }> }) {
  return <label className="settings-row settings-row--select"><span><strong>{label}</strong><small>{description}</small></span><select value={value} onChange={(event) => onChange(event.target.value)}>{options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>;
}

export function ChatBubbles({ bubbles, researchResults = [] }: { bubbles: ChatBubble[]; researchResults?: ResearchResultData[] }) {
  const completedResearchTurns = new Set(researchResults.map((result) => result.turnId).filter((turnId): turnId is string => Boolean(turnId)));
  const displayBubbles = bubbles.filter((bubble) => !(bubble.role === "charlie" && bubble.turnId
    && completedResearchTurns.has(bubble.turnId) && !bubble.id.startsWith("research:")));
  for (const result of researchResults) {
    const reportBubbleId = `research:${result.id}`;
    if (displayBubbles.some((bubble) => bubble.id === reportBubbleId)) continue;
    const reportBubble: ChatBubble = {
      id: reportBubbleId,
      role: "charlie",
      text: result.answer || result.text,
      ...(result.turnId !== undefined ? { turnId: result.turnId } : {}),
    };
    const turnBubbleIndex = result.turnId
      ? displayBubbles.findIndex((bubble) => bubble.role === "charlie" && bubble.turnId === result.turnId && !bubble.id.startsWith("research:"))
      : -1;
    if (turnBubbleIndex >= 0) {
      displayBubbles[turnBubbleIndex] = reportBubble;
      continue;
    }
    const requestIndex = result.turnId
      ? displayBubbles.findIndex((bubble) => bubble.role === "you" && bubble.turnId === result.turnId)
      : -1;
    const queryIndex = requestIndex >= 0 ? requestIndex : displayBubbles.findIndex((bubble) => bubble.role === "you" && bubble.text === result.query);
    const nextRequest = queryIndex < 0 ? -1 : displayBubbles.findIndex((bubble, index) => index > queryIndex && bubble.role === "you");
    displayBubbles.splice(nextRequest < 0 ? displayBubbles.length : nextRequest, 0, reportBubble);
  }
  const scrollRef = useRef<HTMLDivElement>(null);
  const followOutput = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  useEffect(() => {
    const node = scrollRef.current;
    if (node && followOutput.current) node.scrollTop = node.scrollHeight;
  }, [bubbles, researchResults]);
  if (!displayBubbles.length) return null;
  const rich = displayBubbles.some((bubble) => bubble.id.startsWith("research:"))
    || bubbles.slice(-2).some((bubble) => bubble.role === "charlie" && bubble.text.length > 900);
  return <div className="chat-thread">
    <div ref={scrollRef} className={`chat-bubbles ${rich ? "is-rich" : ""}`} tabIndex={0} aria-label="Conversation history" aria-live="polite"
      onScroll={(event) => {
        const node = event.currentTarget;
        followOutput.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 56;
        setShowLatest(!followOutput.current);
      }}>
      {displayBubbles.map((bubble) => {
      const result = researchResults.find((item) => bubble.id === `research:${item.id}`);
      return <div className={`chat-bubble chat-bubble--${bubble.role}${result ? " chat-bubble--research" : ""}`} key={bubble.id}>
        {bubble.role === "charlie" ? <ResearchPanel inline result={result ?? { id: bubble.id, query: "", text: bubble.text }} onClose={() => {}} />
          : bubble.text}
      </div>;
      })}
    </div>
    {showLatest && <button type="button" className="chat-latest" onClick={() => {
      const node = scrollRef.current;
      if (node) node.scrollTop = node.scrollHeight;
      followOutput.current = true;
      setShowLatest(false);
    }}>Jump to latest</button>}
  </div>;
}

function formatWidgetDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function weatherCodeLabel(code?: number): string {
  if (code === undefined) return "Current weather";
  if (code === 0) return "Clear sky";
  if (code === 1) return "Mostly clear";
  if (code === 2) return "Partly cloudy";
  if (code === 3) return "Overcast";
  if (code === 45 || code === 48) return "Fog";
  if (code >= 51 && code <= 67 || code >= 80 && code <= 82) return "Rain";
  if (code >= 71 && code <= 77 || code === 85 || code === 86) return "Snow";
  if (code >= 95) return "Thunderstorm";
  return "Current weather";
}

export function DashboardWidgetsPanel({ widgets, sessionId, chatOpen = false, researching, onWidgetVisibilityChange, onUpdateTimer, onStopwatchAction, onMediaCommand }: {
  widgets?: DashboardWidgets;
  sessionId: string;
  chatOpen?: boolean;
  researching: boolean;
  onWidgetVisibilityChange: (widget: "clock" | "weather" | "timer" | "stopwatch" | "music" | "system_status", visible: boolean, widgetId?: string) => Promise<void>;
  onUpdateTimer: (scheduleId: string, text: string, delaySeconds: number, deliveryChannel: "web" | "voice") => Promise<void>;
  onStopwatchAction: (action: "start" | "stop" | "reset") => Promise<void>;
  onMediaCommand: (action: "previous track" | "play/pause" | "next track") => Promise<void>;
}) {
  type WidgetId = "clock" | "weather" | "stopwatch" | "music" | "system_status" | `timer:${string}`;
  type Position = { left: number; top: number };
  type Positions = Partial<Record<WidgetId, Position>>;
  const [now, setNow] = useState(() => Date.now());
  const [positions, setPositions] = useState<Positions>({});
  const [dismissed, setDismissed] = useState<Partial<Record<WidgetId, string>>>({});
  const [editingTimerId, setEditingTimerId] = useState<string | null>(null);
  const [timerDraft, setTimerDraft] = useState({ text: "", seconds: "" });
  const [stopwatchEditing, setStopwatchEditing] = useState(false);
  const [editError, setEditError] = useState("");
  const [mediaError, setMediaError] = useState("");
  const [mediaBusy, setMediaBusy] = useState(false);
  const widgetRefs = useRef<Partial<Record<WidgetId, HTMLElement | null>>>({});
  const dragRef = useRef<{ widgetId: WidgetId; pointerId: number; x: number; y: number; left: number; top: number } | null>(null);
  const positionKey = `charlie-dashboard-widget-positions:${sessionId}`;
  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, []);
  useEffect(() => {
    try {
      const saved: unknown = JSON.parse(window.localStorage.getItem(positionKey) ?? "null");
      const next: Positions = {};
      if (saved && typeof saved === "object") for (const [key, value] of Object.entries(saved)) {
        const validId = key === "clock" || key === "weather" || key === "stopwatch" || key === "music" || key === "system_status" || key.startsWith("timer:");
        if (validId && value && typeof value === "object"
          && Number.isFinite((value as { left?: unknown }).left)
          && Number.isFinite((value as { top?: unknown }).top)) {
          next[key as WidgetId] = { left: (value as Position).left, top: (value as Position).top };
        }
      }
      setPositions(next);
    } catch { setPositions({}); }
  }, [positionKey]);
  function commitPosition(id: WidgetId, position: Position) {
    setPositions((current) => {
      const next = { ...current, [id]: position };
      try { window.localStorage.setItem(positionKey, JSON.stringify(next)); } catch { /* Position is a convenience. */ }
      return next;
    });
  }
  function dismissWidget(id: WidgetId, signature: string, widget: "clock" | "weather" | "timer" | "stopwatch" | "music" | "system_status", widgetId?: string) {
    setDismissed((current) => ({ ...current, [id]: signature }));
    void onWidgetVisibilityChange(widget, false, widgetId).catch(() => {
      setDismissed((current) => {
        if (current[id] !== signature) return current;
        const next = { ...current };
        delete next[id];
        return next;
      });
    });
  }
  function editTimer(timer: DashboardWidgets["timers"][number]) {
    setEditError("");
    setTimerDraft({
      text: timer.text === "Timer finished" ? "" : timer.text,
      seconds: String(Math.max(1, Math.ceil((Date.parse(timer.dueAt) - now) / 1000))),
    });
    setEditingTimerId(timer.id);
  }
  async function saveTimer(timer: DashboardWidgets["timers"][number]) {
    const seconds = Number(timerDraft.seconds);
    if (!Number.isFinite(seconds) || seconds <= 0) {
      setEditError("Enter a duration greater than zero.");
      return;
    }
    try {
      await onUpdateTimer(timer.id, timerDraft.text.trim() || "Timer finished", seconds, timer.deliveryChannel ?? "web");
      setEditingTimerId(null);
      setEditError("");
    } catch (error) {
      setEditError(error instanceof Error ? error.message : "Timer could not be updated.");
    }
  }
  async function runStopwatchAction(action: "start" | "stop" | "reset") {
    setEditError("");
    try { await onStopwatchAction(action); }
    catch (error) { setEditError(error instanceof Error ? error.message : "Stopwatch could not be updated."); }
  }
  async function runMediaCommand(action: "previous track" | "play/pause" | "next track") {
    setMediaError("");
    setMediaBusy(true);
    try { await onMediaCommand(action); }
    catch (error) { setMediaError(error instanceof Error ? error.message : "Media command was not confirmed."); }
    finally { setMediaBusy(false); }
  }
  const timers = widgets?.timers ?? [];
  const stopwatch = widgets?.stopwatch;
  const weather = widgets?.weather;
  const media = widgets?.media;
  const systemStatus = widgets?.systemStatus;
  const hiddenWidgets = new Set(widgets?.hiddenWidgets ?? []);
  const hiddenWidgetsKey = [...hiddenWidgets].sort().join("|");
  const hiddenByRuntime = (id: WidgetId) => id === "clock"
    ? widgets?.clockVisible === false
    : hiddenWidgets.has(id) || (id.startsWith("timer:") && hiddenWidgets.has("timer"));
  useEffect(() => {
    setDismissed((current) => {
      const next = { ...current };
      let changed = false;
      for (const id of Object.keys(next) as WidgetId[]) if (!hiddenByRuntime(id)) {
        delete next[id];
        changed = true;
      }
      return changed ? next : current;
    });
  }, [hiddenWidgetsKey, widgets?.clockVisible]);
  const hasMediaTrack = Boolean(media?.title || media?.artist) && media?.available !== false;
  const time = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(now);
  const date = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" }).format(now);
  const hasClock = widgets?.clockVisible !== false && dismissed.clock !== "clock";
  const visibleTimers = timers.filter((timer) => !hiddenByRuntime(`timer:${timer.id}`) && dismissed[`timer:${timer.id}`] !== JSON.stringify(timer));
  const weatherSignature = weather ? JSON.stringify(weather) : "";
  const stopwatchSignature = stopwatch ? JSON.stringify(stopwatch) : "";
  const mediaSignature = media ? JSON.stringify(media) : "";
  const systemStatusSignature = JSON.stringify(systemStatus ?? null);
  const showWeather = Boolean(weather && !hiddenByRuntime("weather") && dismissed.weather !== weatherSignature);
  const showStopwatch = Boolean(stopwatch && !hiddenByRuntime("stopwatch") && dismissed.stopwatch !== stopwatchSignature);
  const showMedia = Boolean(media && !hiddenByRuntime("music") && dismissed.music !== mediaSignature);
  const showSystemStatus = Boolean(systemStatus && !hiddenByRuntime("system_status") && dismissed.system_status !== systemStatusSignature);
  const systemMetrics = systemStatus ? [
    { label: "CPU", value: systemStatus.cpuPercent },
    { label: "RAM", value: systemStatus.memoryPercent },
    ...(systemStatus.gpuPercent !== undefined ? [{ label: "GPU", value: systemStatus.gpuPercent }] : []),
    ...(systemStatus.diskPercent !== undefined ? [{ label: "Disk", value: systemStatus.diskPercent }] : []),
  ] : [];
  const activeWidgetIds: WidgetId[] = [
    ...(hasClock ? ["clock" as const] : []),
    ...visibleTimers.map((timer) => `timer:${timer.id}` as WidgetId),
    ...(showWeather ? ["weather" as const] : []),
    ...(showStopwatch ? ["stopwatch" as const] : []),
    ...(showMedia ? ["music" as const] : []),
    ...(showSystemStatus ? ["system_status" as const] : []),
  ];
  const activeWidgetKey = activeWidgetIds.join("|");
  const defaultPosition = (id: WidgetId): Position => {
    const cardWidth = id === "music" ? 280 : 240;
    const center = Math.max(8, Math.min(window.innerWidth - cardWidth - 8, (window.innerWidth - cardWidth) / 2));
    if (window.innerWidth < 760) {
      if (id === "clock") return { left: center, top: 64 };
      if (id === "weather") return { left: center, top: 140 };
      if (id === "stopwatch") return { left: center, top: 216 };
      if (id === "music") return { left: center, top: 292 };
      if (id === "system_status") return { left: center, top: 368 + timers.length * 76 };
      const timerIndex = Math.max(0, timers.findIndex((timer) => id === `timer:${timer.id}`));
      return { left: center, top: 368 + timerIndex * 76 };
    }
    const left = Math.max(8, center - 252);
    const right = Math.max(8, Math.min(window.innerWidth - cardWidth - 8, center + 252));
    if (id === "clock") return { left: center, top: 64 };
    if (id === "weather") return { left, top: 64 };
    if (id === "stopwatch") return { left: center, top: 136 };
    if (id === "music") return { left: center, top: 208 };
    if (id === "system_status") return { left: right, top: timers.length > 3 ? 360 : 208 };
    const timerIndex = Math.max(0, timers.findIndex((timer) => id === `timer:${timer.id}`));
    const slots = [
      { left: right, top: 64 },
      { left: right, top: 136 },
      { left, top: 136 },
      { left: right, top: 280 },
    ];
    return slots[timerIndex] ?? { left, top: 208 + Math.floor((timerIndex - 3) / 2) * 76 };
  };
  const widgetStyle = (id: WidgetId): CSSProperties => chatOpen ? {} : ({ position: "absolute", width: id === "music" ? 280 : 240, ...(positions[id] ?? defaultPosition(id)) });
  useEffect(() => {
    setPositions((current) => {
      const next = { ...current };
      for (const id of activeWidgetIds) if (!next[id]) next[id] = defaultPosition(id);
      if (Object.keys(next).length !== Object.keys(current).length) {
        try { window.localStorage.setItem(positionKey, JSON.stringify(next)); } catch { /* Position is a convenience. */ }
      }
      return next;
    });
  }, [activeWidgetKey, positionKey]);
  if (activeWidgetIds.length === 0) return null;
  function startDrag(id: WidgetId, event: ReactPointerEvent<HTMLElement>) {
    if (chatOpen || event.button !== 0) return;
    const rect = widgetRefs.current[id]?.getBoundingClientRect();
    if (!rect) return;
    dragRef.current = { widgetId: id, pointerId: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveByKeyboard(id: WidgetId, event: ReactKeyboardEvent<HTMLElement>) {
    if (chatOpen) return;
    const delta = event.shiftKey ? 24 : 8;
    const movement: Record<string, [number, number]> = {
      ArrowLeft: [-delta, 0], ArrowRight: [delta, 0], ArrowUp: [0, -delta], ArrowDown: [0, delta],
    };
    const offset = movement[event.key];
    if (!offset) return;
    event.preventDefault();
    const rect = widgetRefs.current[id]?.getBoundingClientRect();
    const current = positions[id] ?? defaultPosition(id);
    commitPosition(id, {
      left: Math.max(8, Math.min(window.innerWidth - (rect?.width ?? 240) - 8, current.left + offset[0])),
      top: Math.max(8, Math.min(window.innerHeight - (rect?.height ?? 80) - 8, current.top + offset[1])),
    });
  }

  return <aside
    className={`assistant-widgets ${chatOpen ? "is-chat-open" : ""} ${researching ? "is-progressing" : ""}`}
    aria-label="Dashboard widgets"
    aria-description={chatOpen ? "Widgets are arranged beside chat. Close chat to reposition them." : undefined}
    onPointerMove={(event: ReactPointerEvent<HTMLElement>) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      const rect = widgetRefs.current[drag.widgetId]?.getBoundingClientRect();
      setPositions((current) => ({
        ...current,
        [drag.widgetId]: {
          left: Math.max(8, Math.min(window.innerWidth - (rect?.width ?? 240) - 8, drag.left + event.clientX - drag.x)),
          top: Math.max(8, Math.min(window.innerHeight - (rect?.height ?? 80) - 8, drag.top + event.clientY - drag.y)),
        },
      }));
    }}
    onPointerUp={(event: ReactPointerEvent<HTMLElement>) => {
      const drag = dragRef.current;
      if (drag?.pointerId === event.pointerId) {
        const rect = widgetRefs.current[drag.widgetId]?.getBoundingClientRect();
        if (rect) commitPosition(drag.widgetId, { left: rect.left, top: rect.top });
        dragRef.current = null;
      }
    }}
    onPointerCancel={() => { dragRef.current = null; }}
  >
    {hasClock && <section
      ref={(node) => { widgetRefs.current.clock = node; }}
      data-widget-id="clock"
      className="assistant-widget assistant-widget--clock"
      style={widgetStyle("clock")}
      aria-label="Local clock"
    >
      <div
        className="assistant-widget__drag"
        role="button"
        tabIndex={0}
        aria-label="Move clock widget; use arrow keys to reposition"
        aria-disabled={chatOpen}
        title={chatOpen ? "Close chat to reposition" : "Drag to move · arrow keys to reposition"}
        onPointerDown={(event: ReactPointerEvent<HTMLDivElement>) => startDrag("clock", event)}
        onKeyDown={(event: ReactKeyboardEvent<HTMLDivElement>) => moveByKeyboard("clock", event)}
      >
        <span className="assistant-widget__label">LOCAL TIME</span>
        <span className="assistant-widget__secondary">{date}</span>
      </div>
      <time className="assistant-widget__clock" dateTime={new Date(now).toISOString()}>{time}</time>
      <button className="assistant-widget__close" type="button" aria-label="Close clock widget" title="Close clock widget" onClick={() => dismissWidget("clock", "clock", "clock")}><X size={13} weight="bold" /></button>
    </section>}
    {showWeather && weather && <section
      ref={(node) => { widgetRefs.current.weather = node; }}
      data-widget-id="weather"
      className="assistant-widget assistant-widget--weather"
      style={widgetStyle("weather")}
      aria-label={`Weather for ${weather.location}`}
    >
      <header
        className="assistant-widget__drag"
        role="button"
        tabIndex={0}
        aria-label="Move weather widget; use arrow keys to reposition"
        aria-disabled={chatOpen}
        title={chatOpen ? "Close chat to reposition" : "Drag to move · arrow keys to reposition"}
        onPointerDown={(event: ReactPointerEvent<HTMLDivElement>) => startDrag("weather", event)}
        onKeyDown={(event: ReactKeyboardEvent<HTMLDivElement>) => moveByKeyboard("weather", event)}
      ><span className="assistant-widget__label">{weather.location}</span><span className="assistant-widget__secondary">{weatherCodeLabel(weather.weatherCode)}</span></header>
      <strong className="assistant-widget__temperature">{weather.temperature}{weather.temperatureUnit}</strong>
      <div className="assistant-widget__metrics">
        {weather.feelsLike !== undefined && <span><small>Feels like</small><strong>{weather.feelsLike}{weather.temperatureUnit}</strong></span>}
        {weather.sunset && <span><small>Sunset</small><strong>{weather.sunset.slice(11, 16)}</strong></span>}
        {weather.windSpeed !== undefined && <span><small>Wind</small><strong>{weather.windSpeed} {weather.windSpeedUnit}</strong></span>}
      </div>
      {weather.observedAt && <span className="assistant-widget__updated">Updated {weather.observedAt.replace("T", " ").slice(0, 16)}</span>}
      <button className="assistant-widget__close" type="button" aria-label="Close weather widget" title="Close weather widget" onClick={() => dismissWidget("weather", weatherSignature, "weather")}><X size={13} weight="bold" /></button>
    </section>}
    {visibleTimers.map((timer) => {
      const id = `timer:${timer.id}` as WidgetId;
      const signature = JSON.stringify(timer);
      const remaining = Math.max(0, Math.ceil((Date.parse(timer.dueAt) - now) / 1000));
      const unnamed = !timer.text || timer.text === "Timer finished";
      return <section
        ref={(node) => { widgetRefs.current[id] = node; }}
        key={timer.id}
        data-widget-id={id}
        className="assistant-widget assistant-widget--timer"
        style={widgetStyle(id)}
        aria-label={`Timer ${timer.text === "Timer finished" ? "finishing in" : timer.text}`}
      >
      <div
        className="assistant-widget__drag"
        role="button"
        tabIndex={0}
        aria-label={`Move timer ${timer.text}; use arrow keys to reposition`}
        aria-disabled={chatOpen}
        title={chatOpen ? "Close chat to reposition" : "Drag to move · arrow keys to reposition"}
        onPointerDown={(event: ReactPointerEvent<HTMLDivElement>) => startDrag(id, event)}
        onKeyDown={(event: ReactKeyboardEvent<HTMLDivElement>) => moveByKeyboard(id, event)}
      ><span className="assistant-widget__label">TIMER</span></div>
      {editingTimerId === timer.id ? <form className="assistant-widget__editor" onSubmit={(event) => { event.preventDefault(); void saveTimer(timer); }}>
        <label>Label <input value={timerDraft.text} maxLength={120} onChange={(event) => setTimerDraft((draft) => ({ ...draft, text: event.target.value }))} placeholder="Optional" /></label>
        <label>Seconds remaining <input type="number" min="1" step="1" value={timerDraft.seconds} onChange={(event) => setTimerDraft((draft) => ({ ...draft, seconds: event.target.value }))} /></label>
        <div className="assistant-widget__edit-actions"><button type="submit">Save</button><button type="button" onClick={() => { setEditingTimerId(null); setEditError(""); }}>Cancel</button></div>
        {editError && <span className="assistant-widget__error" role="alert">{editError}</span>}
      </form> : <div className="assistant-widget__timer">
        <span>{unnamed ? "Finishing in" : timer.text}</span><strong>{formatWidgetDuration(remaining)}</strong>
      </div>}
      <div className="assistant-widget__actions">
        {editingTimerId !== timer.id && <button type="button" aria-label={`Edit timer ${timer.text}`} title="Edit timer" onClick={() => editTimer(timer)}>Edit</button>}
        <button className="assistant-widget__close" type="button" aria-label="Close timer widget" title="Close timer widget" onClick={() => dismissWidget(id, signature, "timer", timer.id)}><X size={13} weight="bold" /></button>
      </div>
    </section>;
    })}
    {showMedia && media && <section
      ref={(node) => { widgetRefs.current.music = node; }}
      data-widget-id="music"
      className="assistant-widget assistant-widget--music"
      style={widgetStyle("music")}
      aria-label="Now playing"
    >
      <div
        className="assistant-widget__drag"
        role="button"
        tabIndex={0}
        aria-label="Move music widget; use arrow keys to reposition"
        aria-disabled={chatOpen}
        title={chatOpen ? "Close chat to reposition" : "Drag to move · arrow keys to reposition"}
        onPointerDown={(event: ReactPointerEvent<HTMLDivElement>) => startDrag("music", event)}
        onKeyDown={(event: ReactKeyboardEvent<HTMLDivElement>) => moveByKeyboard("music", event)}
      ><span className="assistant-widget__label">NOW PLAYING</span></div>
      {hasMediaTrack ? <div className="assistant-widget__media-main">
        {media.artUri && <img className="assistant-widget__media-art" src={media.artUri} alt="" aria-hidden="true" />}
        <div className="assistant-widget__media-copy">
          <strong className="assistant-widget__music-title" title={media.title || "Unknown title"}>{media.title || "Unknown title"}</strong>
          <span className="assistant-widget__secondary">{media.artist || "Unknown artist"}</span>
        </div>
      </div> : <span className="assistant-widget__secondary">{media.adapterAvailable === false ? "Media controls unavailable" : "No active media session"}</span>}
      <div className="assistant-widget__media-meta" aria-live="polite">
        {media.app && <span className="assistant-widget__secondary">{media.app}</span>}
        <span className="assistant-widget__media-status" data-status={media.status}>{media.status.replaceAll("_", " ")}</span>
      </div>
      <div className="assistant-widget__media-controls" aria-label="Media controls">
        <button type="button" aria-label="Previous track" disabled={(!media.available && !hasMediaTrack) || mediaBusy} onClick={() => void runMediaCommand("previous track")}><SkipBack size={15} weight="fill" aria-hidden="true" /></button>
        <button type="button" aria-label={mediaBusy ? "Updating playback status" : media.status === "playing" ? "Pause playback" : "Resume playback"} aria-busy={mediaBusy} disabled={(!media.available && !hasMediaTrack) || mediaBusy} onClick={() => void runMediaCommand("play/pause")}>
          {media.status === "playing" ? <Pause size={15} weight="fill" aria-hidden="true" /> : <Play size={15} weight="fill" aria-hidden="true" />}
        </button>
        <button type="button" aria-label="Next track" disabled={(!media.available && !hasMediaTrack) || mediaBusy} onClick={() => void runMediaCommand("next track")}><SkipForward size={15} weight="fill" aria-hidden="true" /></button>
      </div>
      {mediaError && <span className="assistant-widget__error" role="alert">{mediaError}</span>}
      <button className="assistant-widget__close" type="button" aria-label="Close media widget" title="Close media widget" onClick={() => dismissWidget("music", mediaSignature, "music")}><X size={13} weight="bold" /></button>
    </section>}
    {showStopwatch && stopwatch && <section
      ref={(node) => { widgetRefs.current.stopwatch = node; }}
      data-widget-id="stopwatch"
      className="assistant-widget assistant-widget--stopwatch"
      style={widgetStyle("stopwatch")}
      aria-label={`Stopwatch ${stopwatch.state}`}
    >
      <div
        className="assistant-widget__drag"
        role="button"
        tabIndex={0}
        aria-label="Move stopwatch widget; use arrow keys to reposition"
        aria-disabled={chatOpen}
        title={chatOpen ? "Close chat to reposition" : "Drag to move · arrow keys to reposition"}
        onPointerDown={(event: ReactPointerEvent<HTMLDivElement>) => startDrag("stopwatch", event)}
        onKeyDown={(event: ReactKeyboardEvent<HTMLDivElement>) => moveByKeyboard("stopwatch", event)}
      ><span className="assistant-widget__label">STOPWATCH</span></div>
      <strong className="assistant-widget__time">{formatWidgetDuration(stopwatch.elapsedSeconds + (stopwatch.state === "running" ? (now - Date.parse(stopwatch.sampledAt)) / 1000 : 0))}</strong>
      <span className="assistant-widget__secondary">{stopwatch.state === "running" ? "Running" : "Stopped"}</span>
      <div className="assistant-widget__actions">
        <button type="button" aria-label="Edit stopwatch" title="Edit stopwatch" onClick={() => { setStopwatchEditing((editing) => !editing); setEditError(""); }}>Edit</button>
        <button className="assistant-widget__close" type="button" aria-label="Close stopwatch widget" title="Close stopwatch widget" onClick={() => dismissWidget("stopwatch", stopwatchSignature, "stopwatch")}><X size={13} weight="bold" /></button>
      </div>
      {stopwatchEditing && <div className="assistant-widget__edit-actions">
        <button type="button" aria-label={stopwatch.state === "running" ? "Pause stopwatch" : "Resume stopwatch"} onClick={() => void runStopwatchAction(stopwatch.state === "running" ? "stop" : "start")}>{stopwatch.state === "running" ? "Pause" : "Resume"}</button>
        <button type="button" aria-label="Reset stopwatch" onClick={() => void runStopwatchAction("reset")}>Reset</button>
        {editError && <span className="assistant-widget__error" role="alert">{editError}</span>}
      </div>}
    </section>}
    {showSystemStatus && <section
      ref={(node) => { widgetRefs.current.system_status = node; }}
      data-widget-id="system_status"
      className="assistant-widget assistant-widget--system-status"
      style={widgetStyle("system_status")}
      aria-label="System status"
    >
      <div
        className="assistant-widget__drag"
        role="button"
        tabIndex={0}
        aria-label="Move system status widget; use arrow keys to reposition"
        aria-disabled={chatOpen}
        title={chatOpen ? "Close chat to reposition" : "Drag to move · arrow keys to reposition"}
        onPointerDown={(event: ReactPointerEvent<HTMLDivElement>) => startDrag("system_status", event)}
        onKeyDown={(event: ReactKeyboardEvent<HTMLDivElement>) => moveByKeyboard("system_status", event)}
      ><span className="assistant-widget__label">SYSTEM STATUS</span></div>
      <div className="assistant-widget__system-metrics" aria-label="Live system usage">
        {systemMetrics.map(({ label, value }) => <div
          key={label}
          className="assistant-widget__system-metric"
          role="meter"
          aria-label={`${label} usage`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={value}
          aria-valuetext={`${value.toFixed(0)} percent`}
        >
          <span className="assistant-widget__system-label">{label}</span>
          <strong>{value.toFixed(0)}%</strong>
          <span className="assistant-widget__system-meter" aria-hidden="true"><span style={{ width: `${value}%` }} /></span>
        </div>)}
      </div>
      {systemStatus?.uptimeSeconds !== undefined && <span className="assistant-widget__secondary">Uptime {formatWidgetDuration(Math.floor(systemStatus.uptimeSeconds))}</span>}
      <button className="assistant-widget__close" type="button" aria-label="Close system status widget" title="Close system status widget" onClick={() => dismissWidget("system_status", systemStatusSignature, "system_status")}><X size={13} weight="bold" /></button>
    </section>}
  </aside>;
}

export function ResearchProgressWidget({ progress, steps = [progress], connected }: { progress: RuntimeEvent; steps?: RuntimeEvent[]; connected: boolean }) {
  const [collapsed, setCollapsed] = useState(false);
  const detailsId = useId();
  return <aside className="research-progress-widget" data-collapsed={collapsed} role="status" aria-label="Research progress" aria-live="polite">
    <div className="research-progress-heading">
      {progress.query && <strong>{progress.query}</strong>}
      <button
        className="research-progress-toggle"
        type="button"
        aria-label={`${collapsed ? "Expand" : "Collapse"} research progress details`}
        aria-expanded={!collapsed}
        aria-controls={detailsId}
        title={`${collapsed ? "Expand" : "Collapse"} research progress details`}
        onClick={() => setCollapsed((value) => !value)}
      ><CaretDown size={14} weight="bold" aria-hidden="true" /></button>
    </div>
    {(progress.sourceCount !== undefined || progress.evidenceCount !== undefined) && <p className="research-acquisition-summary">
      {progress.sourceCount !== undefined && `${progress.sourceCount} pages read`}
      {progress.evidenceCount !== undefined && `${progress.sourceCount !== undefined ? " · " : ""}${progress.evidenceCount} evidence passages`}
    </p>}
    {collapsed && progress.text && <p className="research-progress-current">{progress.text}</p>}
    <ol id={detailsId} className="research-progress-steps" hidden={collapsed}>
      {steps.map((step, index) => <li key={step.id ?? `${step.stage ?? "research"}-${step.timestamp ?? index}`} aria-current={index === steps.length - 1 ? "step" : undefined}>
        <span>{step.stage?.replaceAll("_", " ") || "Research"}</span>
        <p>{step.text}</p>
      </li>)}
    </ol>
    {!connected && <span>Connection lost. Last update: {progress.text}</span>}
  </aside>;
}

function OrbPreview() {
  return (
    <main className="orb-preview">
      <header className="orb-preview__header">
        <div>
          <p className="orb-preview__eyebrow">CHARLIE / THINKING ORBS</p>
          <h1>Every presence state</h1>
        </div>
        <a href="/">Return to Charlie</a>
      </header>
      <section className="orb-preview__grid" aria-label="Thinking Orb previews">
        {ORB_PREVIEW_STATES.map(({ state, label }) => (
          <article className="orb-preview__card" key={state}>
            <CrispThinkingOrb state={state} speed={0.9} />
            <div className="orb-preview__label">{label}</div>
            <code>{state}</code>
          </article>
        ))}
      </section>
    </main>
  );
}

export function LiveCaption({ lines, speaker }: { lines: CaptionLine[]; speaker: CaptionLine["kind"] }) {
  if (!lines.length) return null;
  return <div className="live-caption" aria-live="polite">{lines.map((line) => <div
    className={`live-caption__line${line.kind === speaker ? " is-current" : ""}`}
    aria-label={line.kind === "stt" ? "Your speech" : "Charlie speech"} key={line.id}>{line.text}</div>)}</div>;
}

export function ApprovalPopup({ title, reason, busy, error, onDecision }: {
  title: string; reason: string; busy: boolean; error: string; onDecision: (approved: boolean) => void;
}) {
  const popup = useRef<HTMLElement>(null);
  useEffect(() => { popup.current?.focus(); }, []);
  return <section className="approval-popup" role="dialog" aria-labelledby="approval-title"
    aria-describedby="approval-reason" tabIndex={-1} ref={popup} onKeyDown={(event) => {
      if (event.key === "Escape" && !busy) { event.stopPropagation(); onDecision(false); }
    }}>
    <h2 id="approval-title">{title}</h2>
    <p id="approval-reason">{reason}</p>
    {error && <p className="approval-popup__error" role="alert">{error}</p>}
    <div className="approval-popup__actions">
      <button type="button" disabled={busy} onClick={() => onDecision(false)}>Decline</button>
      <button type="button" className="approval-popup__approve" disabled={busy} onClick={() => onDecision(true)}>
        {busy ? "Sending…" : "Approve"}
      </button>
    </div>
  </section>;
}
