import { useEffect, useState, type FormEvent, type ReactElement } from "react";
import { sendCommand } from "../runtime/bridge";
import { useCharlieStore, type ChatMessage } from "../store/charlie";
import { Panel } from "./Panel";

const _SESSION_STORAGE_KEY = "charlie.active-session-id";

interface SessionResponse {
  session_id?: unknown;
  active_session?: unknown;
}

interface SessionHistoryResponse {
  messages?: unknown;
}

function historyMessages(raw: unknown): ChatMessage[] {
  if (!Array.isArray(raw)) return [];
  return raw.reduce<ChatMessage[]>((messages, item, index) => {
    if (!item || typeof item !== "object") return messages;
    const message = item as Record<string, unknown>;
    const role = message.role === "user" ? "user" : message.role === "assistant" ? "charlie" : null;
    const text = typeof message.content === "string" ? message.content : null;
    if (role && text !== null) messages.push({ id: `history-${index}`, role, text, pending: false });
    return messages;
  }, []);
}

export function Chat(): ReactElement {
  const messages = useCharlieStore((state) => state.chatMessages);
  const addUserMessage = useCharlieStore((state) => state.addUserMessage);
  const setChatMessages = useCharlieStore((state) => state.setChatMessages);
  const [draft, setDraft] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadSession(): Promise<void> {
      let activeSessionId = sessionStorage.getItem(_SESSION_STORAGE_KEY);
      if (!activeSessionId) {
        const response = await fetch("/api/session/active");
        if (!response.ok) return;
        const created = await response.json() as SessionResponse;
        if (typeof created.active_session !== "string" || !created.active_session) return;
        activeSessionId = created.active_session;
        sessionStorage.setItem(_SESSION_STORAGE_KEY, activeSessionId);
      }

      await fetch("/api/session/active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: activeSessionId }),
      });
      sendCommand("session_active", { session_id: activeSessionId });

      const response = await fetch(`/api/sessions/${encodeURIComponent(activeSessionId)}/messages`);
      if (!response.ok || cancelled) return;
      const history = await response.json() as SessionHistoryResponse;
      if (cancelled) return;
      setSessionId(activeSessionId);
      setChatMessages(historyMessages(history.messages));
    }

    void loadSession().catch(() => {
      if (!cancelled) setSessionError(true);
    });
    return () => { cancelled = true; };
  }, [setChatMessages]);

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !sessionId) return;
    sendCommand("chat", { text, session_id: sessionId });
    addUserMessage(text);
    setDraft("");
  }

  return (
    <Panel id="chat" title="Conversation">
      <section className="chat-panel" aria-label="Charlie assistant">
      <header className="chat-header">
        <span className="hud-ring-dot" />
        <strong>Charlie Assistant</strong>
      </header>
      <div className="chat-messages">
        {messages.length === 0 && <p className="chat-empty">{sessionError ? "Chat session is unavailable." : "No messages in this session."}</p>}
        {messages.map((message) => (
          <article className={`chat-message is-${message.role}`} key={message.id}>
            {message.role === "charlie" && <span className="chat-agent-icon">●</span>}
            <div>{message.text.split("\n").map((line, index) => <p key={`${message.id}-${index}`}>{line || <>&nbsp;</>}</p>)}</div>
          </article>
        ))}
      </div>
      <form className="chat-composer" onSubmit={submit}>
        <input aria-label="Message Charlie" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Type a message or command..." />
        <button type="submit" aria-label="Send message" disabled={!sessionId}>Send</button>
      </form>
      </section>
    </Panel>
  );
}
