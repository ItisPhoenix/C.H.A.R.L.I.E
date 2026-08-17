import { useState, useRef, useEffect, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { useCharlieStore, type ChatMessage } from "../../store/charlie";
import { sendCommand } from "../../runtime/bridge";

export function ConversationWorkspace({ workspace: _workspace }: { workspace?: WorkspaceInstance }): ReactElement {
  const chatMessages = useCharlieStore((s) => s.chatMessages);
  const timeline = Array.isArray(chatMessages) ? chatMessages : [];
  const coreState = useCharlieStore((s) => s.coreState);
  const connected = useCharlieStore((s) => s.connected);
  const activeToolApproval = useCharlieStore((s) => s.activeToolApproval);
  const activities = useCharlieStore((s) => s.activities);

  const isThinking = coreState === "thinking" || coreState === "working";
  const [inputVal, setInputVal] = useState("");
  const [sending, setSending] = useState(false);
  const [canonicalSessionId, setCanonicalSessionId] = useState<string>("default");
  const endRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Resolve canonical session ID from backend on mount
  useEffect(() => {
    let isMounted = true;
    void fetch("/api/session/active")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!isMounted || !data || !data.active_session) return;
        setCanonicalSessionId(data.active_session);
      })
      .catch(() => {
        // Fallback default
      });

    return () => {
      isMounted = false;
    };
  }, []);

  // Hydrate session messages on mount or canonical session change
  useEffect(() => {
    if (!canonicalSessionId) return;
    sendCommand("session_active", { session_id: canonicalSessionId });

    let isMounted = true;
    void fetch(`/api/sessions/${canonicalSessionId}/messages`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!isMounted || !data || !Array.isArray(data.messages)) return;
        const mapped: ChatMessage[] = data.messages.map(
          (m: { role: string; content: string }, i: number) => ({
            id: `hist-${i}-${Date.now()}`,
            role: m.role === "user" ? "user" : "charlie",
            text: m.content,
            pending: false,
          })
        );
        if (mapped.length > 0) {
          useCharlieStore.getState().setChatMessages(mapped);
        }
      })
      .catch(() => {
        // Safe fallback
      });

    return () => {
      isMounted = false;
    };
  }, [canonicalSessionId]);

  useEffect(() => {
    if (typeof endRef.current?.scrollIntoView === "function") {
      endRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [timeline.length, activities.length, activeToolApproval, isThinking]);

  const handleSendMessage = async () => {
    const text = inputVal.trim();
    if (!text || sending) return;

    setInputVal("");
    setSending(true);

    // Optimistically add user message to timeline
    useCharlieStore.getState().addUserMessage(text);

    try {
      // 1. Send via primary WebSocket command bridge
      sendCommand("chat", { text, session_id: canonicalSessionId });

      // 2. If WS is disconnected, fallback to HTTP endpoint
      if (!connected) {
        await fetch(`/api/sessions/${canonicalSessionId}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
      }
    } catch {
      // Error handling handled by store / alert events
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

  const handleToolApprove = (requestId: string) => {
    sendCommand("tool_approve", { request_id: requestId });
    useCharlieStore.getState().setActiveToolApproval(null);
  };

  const handleToolReject = (requestId: string) => {
    sendCommand("tool_reject", { request_id: requestId });
    useCharlieStore.getState().setActiveToolApproval(null);
  };

  return (
    <div className="w-full h-full flex flex-col justify-between font-mono select-none text-left p-2 overflow-hidden space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-cyan-500/20 pb-2 px-1">
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
            SESSION: <strong className="text-cyan-200">{canonicalSessionId}</strong>
          </span>
        </div>
        <div className="flex items-center gap-2">
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

      {/* Message Stream */}
      <div className="flex-1 w-full p-4 rounded-xl border border-cyan-500/20 bg-slate-950/70 backdrop-blur-md overflow-y-auto space-y-4">
        {timeline.length === 0 ? (
          <div className="text-xs text-slate-500 italic py-8 text-center">
            No conversation messages yet. Send a prompt to start dialogue.
          </div>
        ) : (
          timeline.map((msg, idx) => {
            const isUser = msg.role === "user";
            return (
              <div
                key={msg.id || idx}
                className={`flex flex-col gap-1 max-w-[85%] ${
                  isUser ? "ml-auto items-end" : "mr-auto items-start"
                }`}
              >
                <div className="flex items-center gap-1.5 text-[9px] text-cyan-400/70 uppercase">
                  <span>{isUser ? "OPERATOR" : "C.H.A.R.L.I.E."}</span>
                  {msg.pending && (
                    <span className="text-cyan-400 animate-pulse text-[8px]">[streaming...]</span>
                  )}
                </div>
                <div
                  className={`p-3 rounded-xl text-xs leading-relaxed font-sans whitespace-pre-wrap ${
                    isUser
                      ? "bg-cyan-950/70 border border-cyan-400/40 text-cyan-100 shadow-sm shadow-cyan-500/10"
                      : "bg-slate-900/80 border border-cyan-500/20 text-slate-200"
                  }`}
                >
                  {msg.text}
                </div>
              </div>
            );
          })
        )}

        {/* Live Subsystem Activities / Tools Progress */}
        {activities.length > 0 && (
          <div className="p-2.5 rounded-lg bg-slate-900/60 border border-cyan-500/15 text-[11px] text-cyan-300/80 space-y-1">
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
          <div className="p-3.5 rounded-xl bg-amber-950/40 border border-amber-500/40 text-amber-200 text-xs space-y-2.5">
            <div className="flex items-center gap-2 font-bold text-amber-400 uppercase text-[11px]">
              <span className="w-2 h-2 rounded-full bg-amber-400 animate-ping" />
              Approval Required: {activeToolApproval.tool_name}
            </div>
            <p className="text-[11px] text-slate-300 font-sans">{activeToolApproval.reason}</p>
            {activeToolApproval.arguments && (
              <pre className="p-2 rounded bg-black/60 text-[10px] text-slate-300 overflow-x-auto font-mono">
                {JSON.stringify(activeToolApproval.arguments, null, 2)}
              </pre>
            )}
            <div className="flex gap-2 pt-1">
              <button
                type="button"
                onClick={() => handleToolApprove(activeToolApproval.request_id)}
                className="px-3 py-1 text-xs font-bold rounded bg-emerald-950 border border-emerald-500/50 text-emerald-300 hover:bg-emerald-900 transition cursor-pointer"
              >
                Approve Action
              </button>
              <button
                type="button"
                onClick={() => handleToolReject(activeToolApproval.request_id)}
                className="px-3 py-1 text-xs font-bold rounded bg-rose-950 border border-rose-500/50 text-rose-300 hover:bg-rose-900 transition cursor-pointer"
              >
                Reject
              </button>
            </div>
          </div>
        )}

        <div ref={endRef} />
      </div>

      {/* Input bar */}
      <div className="p-2 rounded-xl border border-cyan-500/25 bg-slate-950/90 flex items-end gap-3">
        <textarea
          ref={textareaRef}
          rows={2}
          value={inputVal}
          onChange={(e) => setInputVal(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Send prompt to Charlie... (Enter to send, Shift+Enter for newline)"
          className="flex-1 bg-transparent border-none outline-none text-xs text-slate-200 placeholder-slate-500 font-sans resize-none focus:ring-0 leading-relaxed"
        />
        <div className="flex flex-col gap-1">
          <button
            type="button"
            disabled={!inputVal.trim() || sending}
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
