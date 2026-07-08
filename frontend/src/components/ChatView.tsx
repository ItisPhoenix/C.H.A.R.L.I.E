"use client";

import { useEffect, useRef, useState, type ReactElement } from "react";
import { useCharlieStore } from "../store/useCharlieStore";

interface ChatViewProps {
  messages: { id?: string; role: string; content: string }[];
  onSend: (text: string) => void;
  loading: boolean;
  sessionTitle?: string;
}

function TypingDots(): ReactElement {
  return (
    <span className="inline-flex gap-1.5 ml-1" aria-label="Charlie is typing">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-[var(--color-aurora)] animate-pulse"
          style={{ animationDelay: `${i * 180}ms` }}
        />
      ))}
    </span>
  );
}

export function ChatView({
  messages,
  onSend,
  loading,
  sessionTitle,
}: ChatViewProps): ReactElement {
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const connected = useCharlieStore((s) => s.connected);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading]);

  const last = messages.length > 0 ? messages[messages.length - 1] : undefined;
  const showTyping = loading && last?.role === "assistant" && !last.content;

  const submit = (): void => {
    const text = input.trim();
    if (!text) return;
    onSend(text);
    setInput("");
  };

  return (
    <section className="glass anim-rise relative flex flex-col h-full overflow-hidden rounded-3xl shadow-[0_18px_50px_rgba(2,4,12,0.55)]">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-glass-border)]">
        <div className="min-w-0">
          <h1 className="font-display text-lg font-semibold text-[var(--color-text-primary)] truncate">
            {sessionTitle || "Charlie"}
          </h1>
          <p className="text-xs text-[var(--color-text-muted)] font-mono">
            Assistant Control Center
          </p>
        </div>
        <span className="flex items-center gap-2 text-xs font-mono uppercase tracking-widest text-[var(--color-text-secondary)]">
          <span
            className={`w-2 h-2 rounded-full ${
              connected ? "bg-status-speaking" : "bg-status-idle"
            }`}
            aria-hidden="true"
          />
          {connected ? "Online" : "Offline"}
        </span>
      </header>

      {/* Messages */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-6 py-5 space-y-4 scrollbar"
      >
        {messages.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center text-center text-[var(--color-text-muted)]">
            <p className="font-display text-xl text-[var(--color-text-secondary)]">
              Start a conversation
            </p>
            <p className="text-sm mt-1">
              Messages you send appear here in real time.
            </p>
          </div>
        )}

        {messages.map((m, i) => {
          const isUser = m.role === "user";
          return (
            <div
              key={m.id ?? `${m.role}-${i}`}
              className={`flex ${isUser ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`anim-message max-w-[78%] px-4 py-3 rounded-2xl text-[15px] leading-relaxed ${
                  isUser
                    ? "bg-[var(--color-aurora)]/15 border border-[var(--color-aurora)]/30 text-[var(--color-text-primary)]"
                    : "bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] text-[var(--color-text-primary)]"
                }`}
              >
                {m.content || (isUser ? "" : <TypingDots />)}
              </div>
            </div>
          );
        })}
        {showTyping && (
          <div className="flex justify-start">
            <div className="px-4 py-3 rounded-2xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)]">
              <TypingDots />
            </div>
          </div>
        )}
      </div>

      {/* Input bar */}
      <div className="px-6 py-4 border-t border-[var(--color-glass-border)]">
        <div className="flex items-end gap-3 glass-hover rounded-2xl border border-[var(--color-glass-border)] bg-[var(--color-glass-bg-2)] px-4 py-3 transition-colors">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={1}
            placeholder="Message Charlie..."
            aria-label="Message Charlie"
            className="flex-1 bg-transparent resize-none outline-none text-[15px] text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] font-sans max-h-32"
          />
          <button
            onClick={submit}
            disabled={!input.trim()}
            aria-label="Send message"
            className="shrink-0 rounded-xl px-4 py-2 text-sm font-medium bg-[var(--color-aurora)] text-white disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition hover:shadow-[0_0_18px_var(--color-aurora-dim)]"
          >
            Send
          </button>
        </div>
      </div>
    </section>
  );
}
