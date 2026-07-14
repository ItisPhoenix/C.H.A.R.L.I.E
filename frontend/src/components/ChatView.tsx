"use client";

import { useEffect, useRef, useState, type ReactElement } from "react";
import { useCharlieStore, type ToolActivityEntry, rgba, lighten } from "../store/useCharlieStore";

const TYPEWRITER_CHARS_PER_SEC = 1200;

// Assistant text streams in per-sentence chunks (backend batches for TTS
// latency, see CLAUDE.md's flush-boundary design) -- this animates each
// chunk's reveal so it still reads as live typing instead of jumping in.
function useTypewriter(fullText: string, charsPerSecond: number): string {
  const [displayed, setDisplayed] = useState(fullText);
  const revealedRef = useRef(fullText.length);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (fullText.length <= revealedRef.current) {
      revealedRef.current = fullText.length;
      setDisplayed(fullText);
      return;
    }
    let last = performance.now();
    const step = (now: number): void => {
      const dt = (now - last) / 1000;
      last = now;
      revealedRef.current = Math.min(fullText.length, revealedRef.current + dt * charsPerSecond);
      setDisplayed(fullText.slice(0, Math.floor(revealedRef.current)));
      if (revealedRef.current < fullText.length) {
        rafRef.current = requestAnimationFrame(step);
      }
    };
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [fullText, charsPerSecond]);

  return displayed;
}

interface ChatViewProps {
  messages: { id?: string; role: string; content: string }[];
  onSend: (text: string) => void;
  onStop?: () => void;
  loading: boolean;
  voiceState?: string;
  toolActivity?: ToolActivityEntry[];
}

function TypingDots(): ReactElement {
  const accentColor = useCharlieStore((s) => s.accentColor);
  return (
    <span className="inline-flex gap-1.5 ml-1" aria-label="Charlie is typing">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-1.5 h-1.5 rounded-full"
          style={{
            background: accentColor,
            animation: "dotPulse 1.2s ease-in-out infinite",
            animationDelay: `${i * 180}ms`,
          }}
        />
      ))}
    </span>
  );
}

export function ChatView({
  messages,
  onSend,
  onStop,
  loading,
  voiceState = "idle",
  toolActivity,
}: ChatViewProps): ReactElement {
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const isSubmittingRef = useRef(false);
  const connected = useCharlieStore((s) => s.connected);
  const accentColor = useCharlieStore((s) => s.accentColor);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading]);

  const last = messages.length > 0 ? messages[messages.length - 1] : undefined;
  const showTyping = loading && last?.role === "assistant" && !last.content;

  const submit = (): void => {
    if (isSubmittingRef.current) return;
    const text = input.trim();
    if (!text) return;
    isSubmittingRef.current = true;
    onSend(text);
    setInput("");
    setTimeout(() => { isSubmittingRef.current = false; }, 500);
  };

  const accentDim = rgba(accentColor, 0.12);
  const accentBorder = rgba(accentColor, 0.25);

  return (
    <section className="glass anim-rise relative flex flex-col h-full overflow-hidden rounded-2xl">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-glass-border)]">
        <div className="min-w-0">
          <h1 className="font-display text-lg font-semibold text-[var(--color-text-primary)] truncate">
            Charlie
          </h1>
          <p className="text-xs text-[var(--color-text-muted)] font-mono">
            Assistant Control Center
          </p>
        </div>
        <span className="flex items-center gap-2 text-xs font-mono uppercase tracking-widest text-[var(--color-text-secondary)]">
          <span
            className={`w-2 h-2 rounded-full ${
              connected ? "bg-[var(--color-accent-teal)] animate-pulse" : "bg-status-idle"
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
                style={{
                  background: isUser ? accentDim : "rgba(255,255,255,0.04)",
                  borderColor: isUser ? accentBorder : "rgba(255,255,255,0.07)",
                }}
                className={`anim-message max-w-[78%] px-4 py-3 rounded-2xl text-[15px] border leading-relaxed text-[var(--color-text-primary)]`}
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

      {/* Tool activity (collapsible) */}
      {toolActivity && toolActivity.length > 0 && (
        <details className="tool-activity glass rounded-lg p-2 mx-6 mb-2 text-xs">
          <summary className="cursor-pointer text-[var(--color-text-secondary)] select-none">
            {toolActivity.length} tool {toolActivity.length === 1 ? "action" : "actions"}
          </summary>
          <ul className="mt-1 space-y-1">
            {toolActivity.map((t, i) => (
              <li key={i} className="font-mono">
                {t.kind === "tool_call" ? "🔧 Ran" : t.kind === "tool_result" ? "↩" : "💭"}{" "}
                {t.name}
                {t.text ? ` → ${t.text}` : ""}
              </li>
            ))}
          </ul>
        </details>
      )}

      {/* Input bar */}
      <div className="px-6 py-4 border-t border-[var(--color-glass-border)]">
        <div className="flex items-center gap-3 glass-hover rounded-2xl border border-[var(--color-glass-border)] bg-[var(--color-glass-bg-2)] px-4 py-2 transition-colors">
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
            className="flex-1 bg-transparent resize-none outline-none text-[15px] text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] font-sans py-1 max-h-32"
          />
          {voiceState !== "idle" ? (
            <button
              onClick={onStop}
              aria-label="Stop generation"
              className="shrink-0 rounded-xl px-4 py-1.5 text-xs font-semibold uppercase tracking-wider bg-red-500/16 text-red-400 border border-red-500/30 cursor-pointer transition hover:bg-red-500/26 hover:text-red-300"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!input.trim()}
              aria-label="Send message"
              style={{
                background: !input.trim() ? "transparent" : accentColor,
                color: !input.trim() ? "#6b7280" : "#03151a",
                border: !input.trim() ? "1px solid rgba(255,255,255,0.07)" : "none",
                opacity: !input.trim() ? 0.4 : 1,
              }}
              className="shrink-0 rounded-xl px-4 py-1.5 text-xs font-semibold uppercase tracking-wider disabled:cursor-not-allowed cursor-pointer transition hover:opacity-80"
            >
              Send
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
