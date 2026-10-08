import { useEffect, useRef, useState, type CSSProperties, type FormEvent, type ReactNode, type Dispatch, type SetStateAction } from "react";
import { BorderBeam } from "border-beam";
import { Liquid } from "liquid-gooey";
import { ChatCircleText } from "@phosphor-icons/react/dist/csr/ChatCircleText";
import { GearSix } from "@phosphor-icons/react/dist/csr/GearSix";
import { MicrophoneSlash } from "@phosphor-icons/react/dist/csr/MicrophoneSlash";
import { Pulse } from "@phosphor-icons/react/dist/csr/Pulse";
import type { OrbState } from "thinking-orbs";
import { VoiceBeam } from "voice-glow";
import { loadScene, orbStateForEvent, parseRuntimeEvent, postCommand, postRuntimeCommand, type ResearchResultData, type ResearchSource, type RuntimeEvent, type RuntimeSetting, type SceneSnapshot } from "./runtime";
import { readDashboardViewState, writeDashboardViewState, type DashboardViewState } from "./sessionView";
import { SettingsSectionNav, type SettingsSectionId } from "./SettingsSectionNav";
import { ResearchTabs } from "./ResearchTabs";
import { Microphone } from "@phosphor-icons/react/dist/csr/Microphone";
import { CrispThinkingOrb } from "./CrispThinkingOrb";

type ActivityItem = RuntimeEvent & { key: string };
type CaptionLine = { id: string; text: string; kind: "stt" | "tts" };
type ChatBubble = { id: string; role: "you" | "charlie"; text: string };
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
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [approvalError, setApprovalError] = useState("");
  const [chatBubbles, setChatBubbles] = useState<ChatBubble[]>([]);
  const [orbState, setOrbState] = useState<OrbState>("connecting");
  const [workPhase, setWorkPhase] = useState<"working" | "solving">("working");
  const [menuOpen, setMenuOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [researchOpen, setResearchOpen] = useState(false);
  const [selectedResearchResultId, setSelectedResearchResultId] = useState<string | null>(null);
  const [dismissedResearchResultIds, setDismissedResearchResultIds] = useState<string[]>([]);
  const [viewHydratedSessionId, setViewHydratedSessionId] = useState<string | null>(null);
  const [activeSettingsSection, setActiveSettingsSection] = useState<SettingsSectionId>("presence");
  const micMuted = scene?.voice?.mic_muted ?? true;
  const researchResults = scene?.researchResults?.length
    ? scene.researchResults
    : scene?.researchResult ? [scene.researchResult] : [];
  const selectedResearchResult = researchResults.find((result) => result.id === selectedResearchResultId)
    ?? scene?.researchResult;
  const [settingsOpen, setSettingsOpen] = useState(false);
  const clearCaptionTimer = useRef<number | undefined>(undefined);
  const audioLevelRef = useRef(0);
  const ttsBufferRef = useRef("");
  const messageInputRef = useRef<HTMLTextAreaElement>(null);
  const [composerWidth, setComposerWidth] = useState(371);
  const replyId = useRef<string | null>(null);
  const presenceRef = useRef<HTMLButtonElement>(null);
  const seenEventIds = useRef(new Set<string>());
  const hydratedSessionRef = useRef<string | null>(null);
  const pendingResearchOpenRef = useRef<string | null>(null);

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

  function addChatBubble(role: ChatBubble["role"], text: string, id: string = crypto.randomUUID()) {
    const clean = text.trim();
    if (!clean) return;
    setChatBubbles((bubbles) => bubbles.some((bubble) => bubble.id === id) ? bubbles : [...bubbles, { id, role, text: clean }]);
  }

  function appendCharlieBubble(text: string) {
    if (!text) return;
    const id = replyId.current ?? (replyId.current = crypto.randomUUID());
    setChatBubbles((bubbles) => {
      const next = [...bubbles];
      const index = next.findIndex((bubble) => bubble.id === id);
      if (index >= 0) next[index] = { ...next[index], text: `${next[index].text}${text}` };
      else {
        next.push({ id, role: "charlie", text });
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
      setStreamOpen(false);
      void loadScene().then((snapshot) => {
        if (!active) return;
        setScene(snapshot);
        setActivity((items) => items.filter((item) => item.type !== "tool_approval_request"
          || item.requestId === snapshot.pendingApproval?.requestId));
        setStreamOpen(true);
        setOrbState((current) => current === "connecting" ? "breathing" : current);
      }).catch(() => { if (active) setStreamOpen(false); });
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
      if (event.id) {
        if (seenEventIds.current.has(event.id)) return;
        seenEventIds.current.add(event.id);
        if (seenEventIds.current.size > 512) {
          seenEventIds.current.delete(seenEventIds.current.values().next().value as string);
        }
      }
      if (event.type === "audio_level" && event.level !== undefined) audioLevelRef.current = event.level;
      if (["speaking_stop", "response_done"].includes(event.type)) audioLevelRef.current = 0;
      const nextOrbState = orbStateForEvent(event);
      if (nextOrbState) setOrbState(nextOrbState);
      if (event.snapshot) setScene((current) => !current || event.snapshot!.revision > current.revision ? event.snapshot! : current);
      if (event.type === "transcript" && event.text) {
        if (!event.partial) addChatBubble("you", event.text);
        addCaption(event.text, "stt", event.partial);
      }
      if (["vad_start", "ptt_start"].includes(event.type)) {
        setCaptionSpeaker("stt");
        if (clearCaptionTimer.current !== undefined) window.clearTimeout(clearCaptionTimer.current);
      }
      if (event.type === "token" && event.channel !== "telegram" && event.text) {
        ttsBufferRef.current += event.text;
        appendCharlieBubble(event.text);
      }
      if (event.type === "research_result" && event.channel !== "telegram") {
        if (event.researchResult) {
          const result = event.researchResult;
          setScene((cur) => {
            if (!cur) return cur;
            const previous = cur.researchResults ?? (cur.researchResult ? [cur.researchResult] : []);
            const next = [...previous.filter((item) => item.id !== result.id), result].slice(-10);
            return { ...cur, researchResult: result, researchResults: next };
          });
          openResearchResult(result.id);
        }
      }
      if (event.type === "result_stored" && event.channel === "web" && event.text) {
        replyId.current = null;
        addChatBubble("charlie", event.text, `task:${event.resultId ?? event.id}`);
      }
      if (["mic_state", "audio_state", "settings_snapshot"].includes(event.type)) {
        void loadScene().then(setScene).catch(() => setStreamOpen(false));
      }
      if (["task_snapshot", "background_task", "research_result", "result_stored"].includes(event.type)) {
        void loadScene().then(setScene).catch(() => setStreamOpen(false));
      }
      if (event.type === "tool_approval_resolved" && event.requestId) {
        setActivity((items) => items.filter((item) => item.requestId !== event.requestId));
        setScene((current) => current && current.pendingApproval?.requestId === event.requestId
          ? { ...current, pendingApproval: undefined } : current);
      }
      if (event.type === "tool_approval_request" && event.channel === "web") {
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
      if (ACTIVITY_TYPES.has(event.type)) {
        setActivity((items) => [{ ...event, key: event.id ?? `${event.type}-${Date.now()}` }, ...items].slice(0, 40));
      }
    };
    void loadScene().then((snapshot) => {
      if (!active) return;
      setScene((current) => !current || snapshot.revision > current.revision ? snapshot : current);
      setSceneError("");
    }).catch((error: unknown) => {
      if (active) setSceneError(error instanceof Error ? error.message : "Scene endpoint is unavailable.");
    });
    return () => {
      active = false;
      stream.close();
      if (clearCaptionTimer.current !== undefined) window.clearTimeout(clearCaptionTimer.current);
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
    addChatBubble("you", text);
    replyId.current = null;
    setMessage("");
    try {
      const accepted = await postCommand(text);
      setCommandNotice(accepted ? "Charlie accepted the command." : "Response received; command acceptance was not confirmed.");
    } catch (error) {
      const failure = error instanceof Error ? error.message : "Command could not be sent.";
      setCommandNotice(failure);
      addChatBubble("charlie", failure);
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
      setScene(await loadScene());
      setCommandNotice("");
    } catch (error) {
      setCommandNotice(error instanceof Error ? error.message : "Runtime control failed.");
    }
  }

  function setVoiceState(type: "set_mic_state" | "set_audio_state", muted: boolean) {
    return sendRuntimeControl({ type, [type === "set_mic_state" ? "mic_muted" : "muted"]: muted });
  }

  async function decideApproval(requestId: string, approved: boolean) {
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
    const updated = await loadScene();
    setScene(updated);
    return updated.settings ?? [];
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
  const runtimeWorking = sending || scene?.conversationState === "working" || Boolean(scene?.activeTurnId) || activeTasks.length > 0;
  const visibleOrbState = !connected ? "connecting" : runtimeWorking
    ? (["searching", "shaping", "weaving", "composing", "listening"].includes(orbState) ? orbState : workPhase)
    : orbState;
  const focusedActivity = activity.filter((item) => !["tool_call", "tool_result"].includes(item.type));
  const visibleActivity = settings.activityDensity === "expanded" || focusedActivity.length === 0 ? activity : focusedActivity;
  const visibleResearchResults = researchResults.filter((result) => !dismissedResearchResultIds.includes(result.id));
  const pendingApproval = activity.find((item) => item.type === "tool_approval_request"
    && item.channel === "web" && item.requestId) ?? scene?.pendingApproval;
  return (
    <PageBeam>
      <main className={`workspace ${chatOpen ? "is-chat-open" : ""} ${drawerOpen ? "is-drawer-open" : ""} ${researchOpen && selectedResearchResult ? "has-research" : ""} ${settings.reducedMotion ? "is-reduced-motion" : ""}`}>
      {!connected && <div className="runtime-notice" role="status">Reconnecting to Charlie. Previous messages are historical.</div>}
      {!chatOpen && commandNotice && <div className="runtime-notice" role="status">{commandNotice}</div>}
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
            <button className="orb-action is-visible" type="button" aria-label={micMuted ? "Unmute microphone" : "Mute microphone"} title={micMuted ? "Unmute microphone" : "Mute microphone"} disabled={!scene?.voice?.enabled} onClick={toggleMute}>{micMuted ? <MicrophoneSlash size={20} weight="regular" aria-hidden="true" /> : <Microphone size={20} weight="regular" aria-hidden="true" />}</button>
          </Liquid.Item>}
          {menuOpen && <Liquid.Item style={{ position: "absolute", left: 238, top: 198 }} x={-170} y={160} transition="bouncy" delay={80}>
            <button className="orb-action is-visible" type="button" aria-label="Activity" title="Activity" onClick={openActivity}><Pulse size={20} weight="regular" aria-hidden="true" /></button>
          </Liquid.Item>}
          {menuOpen && <Liquid.Item style={{ position: "absolute", left: 238, top: 198 }} x={170} y={160} transition="bouncy" delay={120}>
            <button className="orb-action is-visible" type="button" aria-label="Settings" title="Settings" onClick={openSettings}><GearSix size={20} weight="regular" aria-hidden="true" /></button>
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

      {settingsOpen && <SettingsPanel settings={settings} setSettings={setSettings} activeSection={activeSettingsSection} onSelectSection={setActiveSettingsSection} voice={scene?.voice} runtimeSettings={scene?.settings} onSaveSettings={saveRuntimeSettings} onVoiceState={setVoiceState} onClose={() => { setSettingsOpen(false); presenceRef.current?.focus(); }} />}

      {chatOpen && <div className="voice-composer-shell" style={{ "--composer-width": `${composerWidth}px` } as CSSProperties}>
        <ChatBubbles bubbles={chatBubbles.filter((bubble) => !bubble.id.startsWith("research:"))} />
        <VoiceBeam className="voice-composer-beam" type="default" colorVariant="mono" theme="dark" scale={1} level={() => micMuted ? 0 : audioLevelRef.current} processing={runtimeWorking || (!micMuted && visibleOrbState === "working")} strength={0.72} idle={0.42} processingLevel={0.45}>
          <form className="composer composer--open" onSubmit={submit} aria-label="Send a message to Charlie">
            <button className="composer-close" type="button" aria-label="Close Chat" onClick={() => { setChatOpen(false); presenceRef.current?.focus(); }}>×</button>
            <label className="sr-only" htmlFor="message">Message Charlie</label>
            <textarea ref={messageInputRef} id="message" value={message} rows={1} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="Talk to Charlie…" autoComplete="off" />
            <button type="submit" disabled={!message.trim() || sending || !connected}>{sending ? "Sending" : "Send"}</button>
            <p className="command-notice sr-only" aria-live="polite">{commandNotice}</p>
          </form>
        </VoiceBeam>
      </div>}

      {!researchOpen && visibleResearchResults.length > 0 && (
        <ResearchTabs
          results={visibleResearchResults}
          selectedId={selectedResearchResultId}
          onSelect={openResearchResult}
          onDismiss={dismissResearchResult}
        />
      )}

      {researchOpen && selectedResearchResult && (
        <ResearchPanel
          result={selectedResearchResult}
          results={visibleResearchResults}
          selectedId={selectedResearchResultId}
          onSelect={openResearchResult}
          onDismiss={dismissResearchResult}
          onClose={() => setResearchOpen(false)}
        />
      )}

      {pendingApproval?.requestId && <ApprovalPopup
        title={pendingApproval.operationPreview || "Allow this action?"}
        reason={pendingApproval.summary || "Charlie needs your approval before continuing."}
        busy={approvalBusy || !connected} error={approvalError}
        onDecision={(approved) => void decideApproval(pendingApproval.requestId!, approved)}
      />}

      <aside id="activity-panel" className={`activity-panel ${drawerOpen ? "is-open" : ""}`} aria-label="Activity" aria-hidden={!drawerOpen}>
        <div className="drawer-heading"><h2>Activity</h2><button type="button" aria-label="Close activity" onClick={closeActivity}>Close</button></div>
        {activeTasks.length || visibleActivity.length ? <ol className="activity-list">
          {activeTasks.map((task) => <li key={`task-${task.id}`}><strong>{task.title}</strong><span>{task.currentAction || task.status}</span><button type="button" onClick={() => void sendRuntimeControl({ type: "cancel_task", task_id: task.id })}>Cancel task</button>{task.updatedAt && <time dateTime={task.updatedAt}>{task.updatedAt}</time>}</li>)}
          {visibleActivity.map((item) => <li key={item.key}><strong>{activityLabel(item)}</strong>{item.summary && <span>{item.summary}</span>}{item.type === "tool_approval_request" && item.requestId && <div><button type="button" onClick={() => void sendRuntimeControl({ type: "approve", request_id: item.requestId })}>Approve</button><button type="button" onClick={() => void sendRuntimeControl({ type: "reject", request_id: item.requestId })}>Reject</button></div>}{item.timestamp && <time dateTime={item.timestamp}>{item.timestamp}</time>}</li>)}
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
  const editable = (fields ?? []).filter((field) => !/^(MCP_|PLUGIN)/.test(field.key) && !obsolete.has(field.key));
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

function ChatBubbles({ bubbles }: { bubbles: ChatBubble[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => { const node = scrollRef.current; if (node) node.scrollTop = node.scrollHeight; }, [bubbles]);
  if (!bubbles.length) return null;
  const rich = bubbles.slice(-2).some((bubble) => bubble.role === "charlie" && bubble.text.length > 900);
  return <div ref={scrollRef} className={`chat-bubbles ${rich ? "is-rich" : ""}`} tabIndex={0} aria-label="Conversation history" aria-live="polite">
    {bubbles.map((bubble) => <div className={`chat-bubble chat-bubble--${bubble.role}`} key={bubble.id}>{bubble.text.split(/(https?:\/\/[^\s]+)/g).map((part, index) => /^https?:\/\//.test(part) ? <a key={index} href={part} target="_blank" rel="noreferrer">{part}</a> : part)}</div>)}
  </div>;
}

function ResearchPanel({
  result,
  results,
  selectedId,
  onSelect,
  onDismiss,
  onClose,
}: {
  result: ResearchResultData;
  results: ResearchResultData[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDismiss: (id: string) => void;
  onClose: () => void;
}) {
  const marker = result.text.lastIndexOf("\n\nSources:\n");
  const answer = result.answer || (marker >= 0 ? result.text.slice(0, marker) : result.text);
  // Coverage is rendered from the structured gap list below. Drop the legacy
  // markdown footer so partial reports do not explain the same gap twice.
  const rawAnswer = result.gaps?.length
    ? answer.replace(/\n\s*(?:\*{0,2}Gaps and Notes\*{0,2})\s*:\s*[\s\S]*$/i, "").trim()
    : answer;
  const sources: ResearchSource[] = result.sources && result.sources.length > 0 ? result.sources : (
    marker >= 0 ? result.text.slice(marker + "\n\nSources:\n".length).split("\n").flatMap((line) => {
      const match = line.match(/^\[(S\d+)\] (.*): (https?:\/\/\S+)$/);
      if (!match) return [];
      try {
        const url = new URL(match[3]);
        return url.hostname ? [{ id: match[1], title: match[2], url: url.href }] : [];
      } catch { return []; }
    }) : []
  );

  const formatSourceClass = (cls?: string) => {
    switch (cls) {
      case "official": return "Official";
      case "official_store": return "Official Store";
      case "retailer": return "Retailer Price";
      case "official_unverified": return "Unverified Official";
      case "review": return "Review";
      case "news": return "News";
      default: return "";
    }
  };

  const inline = (text: string) => text.split(/(\*\*[^*]+\*\*|\[S\d+\])/g).map((part, index) => {
    if (part.startsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    const source = sources.find((item) => `[${item.id}]` === part);
    return source ? <a className="research-citation" key={index} href={source.url} target="_blank" rel="noreferrer" aria-label={`Source ${source.id}: ${source.title}`}>{part}</a> : part;
  });

  const blocks = rawAnswer.split(/\n\s*\n/).map((block, index) => {
    const lines = block.trim().split("\n");
    if (!block.trim() || /^[-*_]{3,}$/.test(block.trim())) return null;
    if (lines.length > 1 && lines[0].includes("|") && /^[\s|:-]+$/.test(lines[1])) {
      const cells = (line: string) => line.replace(/^\s*\||\|\s*$/g, "").split("|").map((cell) => cell.trim());
      const rows = lines.slice(2).map(cells).filter((row) => row.some((cell) => cell && cell !== "—" && cell !== "-"));
      return <div className="research-table" key={index}><table><thead><tr>{cells(lines[0]).map((cell, i) => <th key={i}>{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, i) => <td key={i}>{inline(cell)}</td>)}</tr>)}</tbody></table></div>;
    }
    if (lines.every((line) => /^\s*(?:[-*]|\d+[.)])\s+/.test(line))) return <ul key={index}>{lines.map((line, i) => <li key={i}>{inline(line.replace(/^\s*(?:[-*]|\d+[.)])\s+/, ""))}</li>)}</ul>;
    const recommendation = /^\s*\*{0,2}Recommendation\*{0,2}\s*:/i.test(block);
    return <div className={recommendation ? "research-recommendation" : undefined} key={index}>{lines.map((line, i) => /^#{1,6}\s+/.test(line) ? <h3 key={i}>{inline(line.replace(/^#{1,6}\s+/, ""))}</h3> : <p key={i}>{inline(line)}</p>)}</div>;
  });

  return (
    <article className="research-panel" aria-label="Research answer">
      <header>
        <div>
          <div className="research-header-badges">
            <span className="research-label">Research</span>
            {result.partial && <span className="research-badge research-badge--partial">Partial</span>}
          </div>
          <h2>{result.query}</h2>
        </div>
        <button type="button" onClick={onClose} aria-label="Close research answer">×</button>
      </header>
      <ResearchTabs
        results={results}
        selectedId={selectedId}
        onSelect={onSelect}
        onDismiss={onDismiss}
        placement="panel"
      />
      <div className="research-body">
        {blocks}
        {result.gaps && result.gaps.length > 0 && (
          <div className="research-gaps">
            <h4>Coverage Notes</h4>
            <ul>
              {result.gaps.map((gap, i) => <li key={i}>{gap}</li>)}
            </ul>
          </div>
        )}
        <details className="research-sources" open>
          <summary>Sources ({sources.length})</summary>
          <ol>
            {sources.map((source) => {
              const chip = formatSourceClass(source.class);
              return (
                <li key={source.id}>
                  <a href={source.url} target="_blank" rel="noreferrer">
                    <span className="research-source-id">{source.id}</span>
                    <span className="research-source-heading">
                      <strong className="research-source-title">{source.title}</strong>
                      {chip && <span className={`research-source-chip research-source-chip--${source.class}`}>{chip}</span>}
                    </span>
                    <small className="research-source-host">{new URL(source.url).hostname}</small>
                  </a>
                </li>
              );
            })}
          </ol>
        </details>
      </div>
    </article>
  );
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
