import { useState, useRef, useEffect, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { useCharlieStore, type ChatMessage } from "../../store/charlie";
import { sendCommand } from "../../runtime/bridge";
import "./ConversationWorkspace.css";

interface SessionSummary {
  id: string;
  title: string;
  updated_at?: string;
}

function sessionTime(value?: string): string {
  if (!value) return "TIME UNAVAILABLE";
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString() : "TIME UNAVAILABLE";
}

export function ConversationWorkspace({ workspace: _workspace }: { workspace?: WorkspaceInstance }): ReactElement {
  const chatMessages = useCharlieStore((s) => s.chatMessages);
  const timeline = Array.isArray(chatMessages) ? chatMessages : [];
  const coreState = useCharlieStore((s) => s.coreState);
  const connected = useCharlieStore((s) => s.connected);
  const activeToolApproval = useCharlieStore((s) => s.activeToolApproval);
  const activities = useCharlieStore((s) => s.activities);
  const projectedSessionId = useCharlieStore((s) => s.activeSessionId);
  const activeSessionTitle = useCharlieStore((s) => s.activeSessionTitle);
  const visualRuntime = useCharlieStore((s) => s.visualRuntime);

  const isThinking = coreState === "thinking" || coreState === "working";
  const [inputVal, setInputVal] = useState("");
  const [sending, setSending] = useState(false);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionListOpen, setSessionListOpen] = useState(false);
  const [sessionSwitching, setSessionSwitching] = useState(false);
  const endRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Resolve canonical session ID from backend on mount
  useEffect(() => {
    let isMounted = true;
    void fetch("/api/session/active")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!isMounted || !data || !data.active_session) return;
        useCharlieStore.getState().applyEvent({
          type: "session_active",
          payload: { session_id: data.active_session },
        });
      })
      .catch(() => {
        // Fallback default only if mount remains active
      });

    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    let isMounted = true;
    void fetch("/api/sessions")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!isMounted || !data || !Array.isArray(data.sessions)) return;
        setSessions(data.sessions.filter((session: SessionSummary) => typeof session?.id === "string"));
      })
      .catch(() => {
        if (isMounted) setSessions([]);
      });
    return () => {
      isMounted = false;
    };
  }, [projectedSessionId]);

  // Hydrate session messages on mount or canonical session change
  useEffect(() => {
    if (!projectedSessionId) return;
    sendCommand("session_active", { session_id: projectedSessionId });

    let isMounted = true;
    void fetch(`/api/sessions/${projectedSessionId}/messages`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!isMounted || !data || !Array.isArray(data.messages)) return;
        const mapped: ChatMessage[] = data.messages.map(
          (m: { id?: string; message_id?: string; role: string; content: string }, i: number) => ({
            id: m.id || m.message_id || `hist-${i}-${m.role}-${m.content.slice(0, 24)}`,
            role: m.role === "user" ? "user" : "charlie",
            text: m.content,
            pending: false,
          })
        );
        if (mapped.length > 0) {
          const live = useCharlieStore.getState().chatMessages;
          const known = new Set(mapped.map((message) => `${message.role}:${message.text}`));
          useCharlieStore.getState().setChatMessages([
            ...live,
            ...mapped.filter((message) => !known.has(`${message.role}:${message.text}`)),
          ]);
        }
      })
      .catch(() => {
        // Safe fallback
      });

    return () => {
      isMounted = false;
    };
  }, [projectedSessionId]);

  const switchSession = async (session: SessionSummary): Promise<void> => {
    if (!session.id || session.id === projectedSessionId || sessionSwitching) return;
    setSessionSwitching(true);
    try {
      const response = await fetch("/api/session/active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: session.id }),
      });
      const result = await response.json().catch(() => ({}));
      const activeId = result?.result?.active_session_id;
      if (!response.ok || result?.status !== "completed" || activeId !== session.id) return;
      useCharlieStore.getState().setChatMessages([]);
      useCharlieStore.getState().applyEvent({
        type: "session_active",
        payload: { session_id: session.id, title: session.title },
      });
      setSessionListOpen(false);
    } finally {
      setSessionSwitching(false);
    }
  };

  useEffect(() => {
    if (typeof endRef.current?.scrollIntoView === "function") {
      endRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [timeline.length, activities.length, activeToolApproval, isThinking]);

  const handleSendMessage = async () => {
    const text = inputVal.trim();
    if (!text || sending || !projectedSessionId) return;

    setInputVal("");
    setSending(true);

    const requestId = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `chat-${Date.now()}`;

    // Optimistically add user message to timeline
    useCharlieStore.getState().addUserMessage(text, requestId);

    try {
      // Select one transport. Disconnected HTTP fallback must not leave a
      // queued WebSocket command that can replay after reconnect.
      if (connected) {
        sendCommand("chat", { text, session_id: projectedSessionId, request_id: requestId });
      } else {
        const response = await fetch(`/api/sessions/${projectedSessionId}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, request_id: requestId }),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !["accepted", "completed"].includes(String(result.status))) {
          useCharlieStore.getState().markUserMessageFailed(requestId);
        }
      }
    } catch {
      useCharlieStore.getState().markUserMessageFailed(requestId);
    } finally {
      setSending(false);
      textareaRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSendMessage();
    }
  };

  const handleInterrupt = () => {
    sendCommand("stop");
    void fetch("/api/stop", { method: "POST" }).catch(() => {});
  };

  return (
    <div className="conversation-workspace w-full h-full flex flex-col justify-between font-mono text-left p-2 overflow-hidden space-y-3 pr-[calc(var(--core-docked-size)+12px)]">
      {/* Header */}
      <div className="conversation-workspace__header flex items-center justify-between pb-2 px-1">
        <div className="flex items-center gap-3">
          <div
            className={`w-2.5 h-2.5 rounded-full ${
              connected ? "bg-cyan-400 animate-pulse" : "bg-amber-400"
            }`}
          />
          <span className="text-xs font-bold text-cyan-300 tracking-wider">
            CONVERSATION & DIALOGUE LOG
          </span>
          <span className="text-[10px] text-slate-400 font-mono">
            SESSION: <strong className="text-cyan-200">{projectedSessionId ?? "CONNECTING..."}</strong>
            {activeSessionTitle && <span className="text-[10px] text-slate-500">[{activeSessionTitle}]</span>}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="px-2 py-1 text-[10px] font-mono border border-cyan-500/30 text-cyan-300 hover:border-cyan-300 disabled:opacity-50"
            aria-expanded={sessionListOpen}
            onClick={() => setSessionListOpen((open) => !open)}
            disabled={sessionSwitching}
          >
            SESSION HISTORY {sessions.length ? `[${sessions.length}]` : ""}
          </button>
          {isThinking && (
            <button
              type="button"
              onClick={handleInterrupt}
              className="px-2.5 py-1 text-[10px] font-bold rounded bg-rose-950/80 border border-rose-500/50 text-rose-300 hover:bg-rose-900 transition cursor-pointer flex items-center gap-1.5"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-rose-400 animate-ping" />
              INTERRUPT / STOP
            </button>
          )}
          <div className="text-[10px] text-slate-400">
            {connected ? (
              <span className="text-emerald-400 font-mono">REALTIME CONNECTED</span>
            ) : (
              <span className="text-amber-400 font-mono">RECONNECTING...</span>
            )}
          </div>
        </div>
      </div>

      {sessionListOpen && (
        <div className="conversation-workspace__history max-h-40 overflow-y-auto p-2 space-y-1" role="listbox" aria-label="Session history">
          {sessions.length === 0 ? (
            <p className="text-[10px] text-slate-500">No session history is available.</p>
          ) : sessions.map((session) => (
            <button
              type="button"
              role="option"
              aria-selected={session.id === projectedSessionId}
              key={session.id}
              className="w-full flex items-center justify-between gap-3 px-2 py-1.5 text-left text-[10px] text-slate-300 hover:bg-cyan-950/50"
              onClick={() => void switchSession(session)}
            >
              <span className="truncate">{session.title || "UNTITLED SESSION"}</span>
              <span className="shrink-0 text-slate-500">{sessionTime(session.updated_at)}</span>
            </button>
          ))}
        </div>
      )}

      {/* Message Stream */}
      <div className="conversation-workspace__stream flex-1 overflow-y-auto min-h-0 space-y-3 pr-1">
        {timeline.length === 0 ? (
          <div className="h-full flex items-center justify-center text-xs text-slate-500 italic">
            No conversation messages yet. Send a prompt to start dialogue.
          </div>
        ) : (
          timeline.map((msg, idx) => {
            const isUser = msg.role === "user";
            const isCurrent = msg.pending || idx === timeline.length - 1;
            return (
              <div
                key={msg.id || idx}
                className={`conversation-workspace__turn flex flex-col space-y-1 ${
                  isUser ? "conversation-workspace__turn--user items-end" : "conversation-workspace__turn--charlie items-start"
                } ${isCurrent ? "conversation-workspace__turn--current" : ""}`}
              >
                <div className="flex items-center gap-1.5 text-[9px] text-cyan-400/70 uppercase">
                  <span>{isUser ? "OPERATOR" : "CHARLIE"}</span>
                  {msg.failed && <span className="text-rose-400 text-[8px]">[failed]</span>}
                  {msg.pending && (
                    <span className="text-cyan-400 animate-pulse text-[8px]">[streaming...]</span>
                  )}
                </div>
                <div
                    className={`conversation-workspace__message max-w-[72ch] p-3 text-xs leading-relaxed font-sans whitespace-pre-wrap break-words select-text ${
                    isUser
                      ? "conversation-workspace__message--user text-cyan-100"
                      : "text-slate-200"
                  }`}
                >
                  {msg.text}
                </div>
              </div>
            );
          })
        )}

        {visualRuntime.phase === "transcribing" && visualRuntime.transcript && (
          <div className="border-l border-cyan-400/50 px-2 text-[10px] text-cyan-200" role="status">
            TRANSCRIPT: {visualRuntime.transcript}
          </div>
        )}

        {/* Live Subsystem Activities / Tools Progress */}
        {activities.length > 0 && (
          <div className="conversation-workspace__activity p-2.5 rounded-lg bg-slate-900/60 border border-cyan-500/15 text-[11px] text-cyan-300/80 space-y-1">
            <div className="text-[9px] text-cyan-400 font-bold uppercase tracking-wider flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
              Active System Actions
            </div>
            {activities.map((act, i) => (
              <div key={i} className="font-mono text-slate-300 text-[10px] pl-2 border-l border-cyan-500/30">
                {act}
              </div>
            ))}
          </div>
        )}

        {/* Pending Tool Approval Card */}
        {activeToolApproval && (
          <div className="conversation-workspace__approval p-3.5 text-amber-200 text-xs space-y-2.5">
            <div className="flex items-center gap-2 font-bold text-amber-400 uppercase text-[11px]">
              <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping" />
              Approval Required: {activeToolApproval.tool_name}
            </div>
            <p className="text-[11px] text-slate-300 font-sans">{activeToolApproval.reason}</p>
            <p className="text-[10px] text-amber-300/80" role="status">
              Detailed approval information is available in the canonical approval dialog.
            </p>
          </div>
        )}

        <div ref={endRef} />
      </div>

      {/* Input bar */}
      <div className="conversation-workspace__composer p-2 rounded-xl border border-cyan-500/25 bg-slate-950/90 flex items-end gap-3">
        <textarea
          ref={textareaRef}
          rows={2}
          value={inputVal}
          onChange={(e) => setInputVal(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
              projectedSessionId
              ? "Send prompt to Charlie... (Enter to send, Shift+Enter for newline)"
              : "Connecting to active session..."
          }
            disabled={!projectedSessionId}
          className="flex-1 bg-transparent border-none outline-none text-xs text-slate-200 placeholder-slate-500 font-sans resize-none focus:ring-0 leading-relaxed disabled:opacity-50"
        />
        <div className="flex flex-col gap-1">
          <button
            type="button"
            disabled={!projectedSessionId || !inputVal.trim() || sending}
            onClick={() => void handleSendMessage()}
            className="px-4 py-2 text-xs font-bold rounded-lg bg-cyan-950 text-cyan-300 border border-cyan-500/40 hover:bg-cyan-900 transition disabled:opacity-40 cursor-pointer"
          >
            {sending ? "Sending..." : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
