import { useEffect, useRef, useState, type CSSProperties, type FormEvent, type ReactNode } from "react";
import { BorderBeam } from "border-beam";
import { Liquid } from "liquid-gooey";
import { ChatCircleText } from "@phosphor-icons/react/dist/csr/ChatCircleText";
import { GearSix } from "@phosphor-icons/react/dist/csr/GearSix";
import { Microphone } from "@phosphor-icons/react/dist/csr/Microphone";
import { MicrophoneSlash } from "@phosphor-icons/react/dist/csr/MicrophoneSlash";
import { Pulse } from "@phosphor-icons/react/dist/csr/Pulse";
import { ThinkingOrb, type OrbState } from "thinking-orbs";
import { VoiceBeam } from "voice-glow";
import { loadScene, orbStateForEvent, parseRuntimeEvent, postCommand, type RuntimeEvent, type SceneSnapshot } from "./runtime";
import { CrispThinkingOrb } from "./CrispThinkingOrb";

type ActivityItem = RuntimeEvent & { key: string };
type CaptionLine = { id: string; text: string; kind: "stt" | "tts" };
type ChatBubble = { id: string; role: "you" | "charlie"; text: string };

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
  const [chatBubbles, setChatBubbles] = useState<ChatBubble[]>(() => {
    try {
      const saved: unknown = JSON.parse(sessionStorage.getItem("charlie-chat") ?? "[]");
      return Array.isArray(saved) ? saved.filter((item) => item && typeof item.id === "string" && ["you", "charlie"].includes(item.role) && typeof item.text === "string") : [];
    } catch { return []; }
  });
  const [orbState, setOrbState] = useState<OrbState>("breathing");
  const [menuOpen, setMenuOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const micMuted = false;
  const [settingsOpen, setSettingsOpen] = useState(false);
  const captionId = useRef(0);
  const clearCaptionTimer = useRef<number | undefined>(undefined);
  const audioLevelRef = useRef(0);
  const ttsBufferRef = useRef("");
  const messageInputRef = useRef<HTMLTextAreaElement>(null);
  const [composerWidth, setComposerWidth] = useState(371);
  const replyId = useRef<string | null>(null);
  const presenceRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    try { sessionStorage.setItem("charlie-chat", JSON.stringify(chatBubbles)); } catch { /* Storage may be disabled or full. */ }
  }, [chatBubbles]);

  function addChatBubble(role: ChatBubble["role"], text: string) {
    const clean = text.trim();
    if (!clean) return;
    setChatBubbles((bubbles) => [...bubbles, { id: crypto.randomUUID(), role, text: clean }]);
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

  function queueCaptionClear(delay: number) {
    if (clearCaptionTimer.current !== undefined) window.clearTimeout(clearCaptionTimer.current);
    clearCaptionTimer.current = window.setTimeout(() => setCaptionLines([]), delay);
  }

  function addCaption(text: string, kind: CaptionLine["kind"]) {
    const clean = text.trim();
    if (!clean) return;
    const parts = clean.split(/\r?\n/).map((part) => part.trim()).filter(Boolean);
    setCaptionLines((lines) => [...lines, ...parts.map((part) => {
      captionId.current += 1;
      return { id: `${kind}-${captionId.current}`, text: part, kind };
    })].slice(-3));
    queueCaptionClear(Math.max(kind === "stt" ? 4200 : 2600, clean.length * 24 + 1800));
  }

  function appendTtsCaption(text: string) {
    if (!text) return;
    setCaptionLines((lines) => {
      const next = [...lines];
      const last = next[next.length - 1];
      if (last?.kind === "tts") next[next.length - 1] = { ...last, text: `${last.text}${text}` };
      else {
        captionId.current += 1;
        next.push({ id: `tts-${captionId.current}`, text, kind: "tts" });
      }
      while (next.length && next[next.length - 1].kind === "tts" && next[next.length - 1].text.length > 64) {
        const current = next[next.length - 1];
        const split = current.text.lastIndexOf(" ", 64);
        if (split < 1) break;
        const remainder = current.text.slice(split + 1).trimStart();
        next[next.length - 1] = { ...current, text: current.text.slice(0, split).trimEnd() };
        if (!remainder) break;
        captionId.current += 1;
        next.push({ id: `tts-${captionId.current}`, text: remainder, kind: "tts" });
      }
      return next.slice(-3);
    });
    queueCaptionClear(Math.max(3200, text.length * 24 + 1800));
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
        setStreamOpen(true);
      }).catch(() => { if (active) setStreamOpen(false); });
    };
    stream.onerror = () => active && setStreamOpen(false);
    stream.onmessage = (messageEvent) => {
      let raw: unknown;
      try { raw = JSON.parse(messageEvent.data); } catch { return; }
      const event = parseRuntimeEvent(raw);
      if (!event) return;
      if (event.type === "audio_level" && event.level !== undefined) audioLevelRef.current = event.level;
      if (["speaking_stop", "response_done"].includes(event.type)) audioLevelRef.current = 0;
      const nextOrbState = orbStateForEvent(event.type);
      if (nextOrbState) setOrbState(nextOrbState);
      if (event.snapshot) setScene((current) => !current || event.snapshot!.revision > current.revision ? event.snapshot! : current);
      if (event.type === "transcript" && event.text) {
        addChatBubble("you", event.text);
        addCaption(event.text, "stt");
      }
      if (event.type === "token" && event.text) {
        ttsBufferRef.current += event.text;
        appendCharlieBubble(event.text);
      }
      if (event.type === "speaking_start") {
        if (ttsBufferRef.current.trim()) appendTtsCaption(ttsBufferRef.current);
        ttsBufferRef.current = "";
      }
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

  function toggleMute() {
    setMenuOpen(false);
    setSettingsOpen(false);
    setCommandNotice("Microphone control is unavailable: native runtime mute is not connected.");
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
  const visibleOrbState = micMuted ? "breathing" : orbState;
  const activeTasks = (scene?.tasks ?? []).filter((task) => !["completed", "failed", "cancelled"].includes(task.status)).slice(0, 24);
  return (
    <PageBeam>
      <main className={`workspace ${chatOpen ? "is-chat-open" : ""} ${drawerOpen ? "is-drawer-open" : ""}`}>
      {!connected && <div className="runtime-notice" role="status">Reconnecting to Charlie. Previous messages are historical.</div>}
      {!chatOpen && commandNotice.startsWith("Microphone") && <div className="runtime-notice" role="status">{commandNotice}</div>}
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
            <button className="orb-action is-visible" type="button" aria-label="Microphone control unavailable" title="Native microphone control is not connected" aria-disabled="true" onClick={toggleMute}>{micMuted ? <Microphone size={20} weight="regular" aria-hidden="true" /> : <MicrophoneSlash size={20} weight="regular" aria-hidden="true" />}</button>
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
        {!chatOpen && <LiveCaption lines={captionLines} />}
        {settingsOpen && <SettingsPanel onClose={() => { setSettingsOpen(false); presenceRef.current?.focus(); }} />}
        {scene ? <article className="scene-content" aria-live="polite">
          <h1>{scene.title}</h1>
          {scene.summary && <p className="scene-summary">{scene.summary}</p>}
          {scene.details.length > 0 && <dl className="scene-details">{scene.details.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>}
        </article> : <p className="empty-state">{sceneError}</p>}
      </section>

      {chatOpen && <div className="voice-composer-shell" style={{ "--composer-width": `${composerWidth}px` } as CSSProperties}>
        <ChatBubbles bubbles={chatBubbles} />
        <VoiceBeam className="voice-composer-beam" type="default" colorVariant="mono" theme="dark" scale={1} level={() => micMuted ? 0 : audioLevelRef.current} processing={sending || (!micMuted && orbState === "working")} strength={0.72} idle={0.42} processingLevel={0.45}>
          <form className="composer composer--open" onSubmit={submit} aria-label="Send a message to Charlie">
            <button className="composer-close" type="button" aria-label="Close Chat" onClick={() => { setChatOpen(false); presenceRef.current?.focus(); }}>×</button>
            <label className="sr-only" htmlFor="message">Message Charlie</label>
            <textarea ref={messageInputRef} id="message" value={message} rows={1} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="Talk to Charlie…" autoComplete="off" />
            <button type="submit" disabled={!message.trim() || sending || !connected}>{sending ? "Sending" : "Send"}</button>
            <p className="command-notice sr-only" aria-live="polite">{commandNotice}</p>
          </form>
        </VoiceBeam>
      </div>}

      <aside id="activity-panel" className={`activity-panel ${drawerOpen ? "is-open" : ""}`} aria-label="Activity" aria-hidden={!drawerOpen}>
        <div className="drawer-heading"><h2>Activity</h2><button type="button" aria-label="Close activity" onClick={closeActivity}>Close</button></div>
        {activeTasks.length || activity.length ? <ol className="activity-list">
          {activeTasks.map((task) => <li key={`task-${task.id}`}><strong>{task.title}</strong><span>{task.currentAction || task.status}</span>{task.updatedAt && <time dateTime={task.updatedAt}>{task.updatedAt}</time>}</li>)}
          {activity.map((item) => <li key={item.key}><strong>{activityLabel(item)}</strong>{item.summary && <span>{item.summary}</span>}{item.timestamp && <time dateTime={item.timestamp}>{item.timestamp}</time>}</li>)}
        </ol> : <p className="activity-empty">No active work or background tasks.</p>}
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

function SettingsPanel({ onClose }: { onClose: () => void }) {
  return (
    <aside className="settings-panel" aria-label="Charlie settings">
      <div className="settings-panel__header">
        <div>
          <span className="settings-panel__eyebrow">CHARLIE</span>
          <h2>Settings</h2>
        </div>
        <button type="button" aria-label="Close Settings" onClick={onClose}>Close</button>
      </div>
      <div className="settings-row"><span>Visual treatment</span><strong>Monochrome</strong></div>
      <div className="settings-row"><span>Voice owner</span><strong>Native Charlie runtime</strong></div>
      <a className="settings-preview-link" href="/?preview=orbs">Preview orb states</a>
    </aside>
  );
}

function ChatBubbles({ bubbles }: { bubbles: ChatBubble[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => { const node = scrollRef.current; if (node) node.scrollTop = node.scrollHeight; }, [bubbles]);
  if (!bubbles.length) return null;
  return <div ref={scrollRef} className="chat-bubbles" tabIndex={0} aria-label="Conversation history" aria-live="polite">
    {bubbles.map((bubble) => <div className={`chat-bubble chat-bubble--${bubble.role}`} key={bubble.id}>{bubble.text}</div>)}
  </div>;
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
            <ThinkingOrb className="orb-preview__orb" state={state} size={64} theme="dark" speed={0.9} aria-label={label} />
            <div className="orb-preview__label">{label}</div>
            <code>{state}</code>
          </article>
        ))}
      </section>
    </main>
  );
}

function LiveCaption({ lines }: { lines: CaptionLine[] }) {
  const targets = useRef(lines);
  const [typedLines, setTypedLines] = useState<CaptionLine[]>([]);

  useEffect(() => {
    targets.current = lines;
    setTypedLines((current) => lines.map((line) => ({ ...line, text: current.find((item) => item.id === line.id)?.text ?? "" })));
  }, [lines]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setTypedLines((current) => {
        const next = targets.current.map((line) => ({ ...line, text: current.find((item) => item.id === line.id)?.text ?? "" }));
        const index = next.findIndex((line) => line.text.length < targets.current.find((target) => target.id === line.id)!.text.length);
        if (index >= 0) {
          const target = targets.current[index].text;
          next[index] = { ...next[index], text: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? target : target.slice(0, next[index].text.length + 1) };
        }
        return next;
      });
    }, 24);
    return () => window.clearInterval(timer);
  }, []);
  if (!typedLines.length) return null;
  return <div className="live-caption" aria-live="polite">{typedLines.map((line) => <div className="live-caption__line" key={line.id}>{line.text}</div>)}</div>;
}
