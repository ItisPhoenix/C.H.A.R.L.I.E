"use client";

import { useEffect, useRef, useState, type ReactElement } from "react";
import {
  Check, Copy, AlertTriangle, ShieldAlert, Sparkles,
  Circle, CheckCircle2, XCircle, Terminal, ChevronDown, ChevronUp
} from "lucide-react";
import { useCharlieStore, type ToolActivityEntry, type RecoveryProposal, type ToolApprovalRequest, rgba } from "../store/useCharlieStore";
import { Button } from "./Button";

function CopyButton({ text }: { text: string }): ReactElement {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button
      onClick={handleCopy}
      className="p-1 rounded hover:bg-white/10 text-slate-400 hover:text-slate-200 transition active:scale-90"
      title="Copy to clipboard"
    >
      {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  );
}

function parseInlineFormatting(text: string): React.ReactNode[] {
  const boldRegex = /(\*\*.*?\*\*|`.*?`)/g;
  const parts = text.split(boldRegex);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={index} className="font-bold text-slate-100">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={index} className="px-1 py-0.5 rounded bg-white/5 border border-white/10 font-mono text-xs text-cyan-300">
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}

function parseInlineMarkdown(text: string): React.ReactNode {
  const paragraphs = text.split("\n");
  return (
    <div className="space-y-2">
      {paragraphs.map((p, idx) => {
        const trimmed = p.trim();
        if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
          const cleanText = trimmed.substring(2);
          return (
            <ul key={idx} className="list-disc list-inside pl-2 space-y-0.5">
              <li className="text-[14px] leading-relaxed text-slate-200">
                {parseInlineFormatting(cleanText)}
              </li>
            </ul>
          );
        }
        return (
          <p key={idx} className="text-[14px] leading-relaxed text-slate-200">
            {parseInlineFormatting(p)}
          </p>
        );
      })}
    </div>
  );
}

function formatMessageContent(content: string): React.ReactNode {
  if (!content) return "";
  const parts = content.split("```");
  if (parts.length < 2) {
    return parseInlineMarkdown(content);
  }

  return (
    <div className="space-y-3 font-sans">
      {parts.map((part, index) => {
        if (index % 2 === 1) {
          const lines = part.split("\n");
          const firstLine = lines[0].trim();
          const language = firstLine || "code";
          const codeText = lines.slice(1).join("\n").trim();
          return (
            <div key={index} className="rounded-lg overflow-hidden border border-white/10 bg-zinc-950 font-mono text-xs my-2">
              <div className="flex items-center justify-between px-3 py-1.5 bg-white/[0.03] border-b border-white/5 text-[10px] text-slate-400 font-sans tracking-wide uppercase select-none">
                <span>{language}</span>
                <CopyButton text={codeText} />
              </div>
              <pre className="p-3 overflow-x-auto text-[var(--color-text-primary)] leading-relaxed scrollbar">
                <code>{codeText}</code>
              </pre>
            </div>
          );
        } else {
          return <div key={index}>{parseInlineMarkdown(part)}</div>;
        }
      })}
    </div>
  );
}

function StepperEntries({ entries }: { entries: ToolActivityEntry[] }): ReactElement {
  return (
    <>
      {entries.map((t, idx) => {
        const isCall = t.kind === "tool_call";
        const isResult = t.kind === "tool_result";
        const isAgentResult = t.kind === "agent_result";
        const agentFailed = isAgentResult && /error|timed out|cancelled/i.test(t.text);

        let bullet = <Circle className="w-3 h-3 text-purple-400 fill-purple-400/20 animate-pulse absolute -left-[6.5px]" />;
        if (isResult) {
          bullet = <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 fill-black absolute -left-[7px]" />;
        } else if (t.kind === "thinking_update") {
          bullet = <Circle className="w-3 h-3 text-cyan-400 fill-cyan-400/20 animate-pulse absolute -left-[6.5px]" />;
        } else if (t.kind === "agent_spawned") {
          bullet = <Circle className="w-3 h-3 text-indigo-400 fill-indigo-400/20 animate-pulse absolute -left-[6.5px]" />;
        } else if (t.kind === "agent_status") {
          bullet = <Circle className="w-3 h-3 text-amber-400 fill-amber-400/20 animate-pulse absolute -left-[6.5px]" />;
        } else if (isAgentResult) {
          bullet = agentFailed
            ? <XCircle className="w-3.5 h-3.5 text-red-400 fill-black absolute -left-[7px]" />
            : <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 fill-black absolute -left-[7px]" />;
        }

        const labelClass = isCall
          ? "bg-purple-950/60 text-purple-300"
          : isResult
            ? "bg-emerald-950/60 text-emerald-300"
            : t.kind === "thinking_update"
              ? "bg-cyan-950/60 text-cyan-300"
              : t.kind === "agent_spawned"
                ? "bg-indigo-950/60 text-indigo-300"
                : t.kind === "agent_status"
                  ? "bg-amber-950/60 text-amber-300"
                  : agentFailed
                    ? "bg-red-950/60 text-red-300"
                    : "bg-emerald-950/60 text-emerald-300";
        const label = t.kind.startsWith("agent_") ? t.kind.replace("agent_", "agent ") : t.kind.replace("tool_", "");

        return (
          <div key={idx} className="relative flex flex-col text-[11px] font-mono leading-relaxed">
            {bullet}
            <div className="flex items-center gap-2">
              <span className={`uppercase text-[10px] px-1 rounded ${labelClass}`}>
                {label}
              </span>
              <span className="text-slate-300 font-semibold">{t.name}</span>
            </div>
            {t.text && (
              <span className="text-slate-500 mt-0.5 pl-2 break-all text-[10px] border-l border-white/5">
                {t.text}
              </span>
            )}
          </div>
        );
      })}
    </>
  );
}

function MessageExecutionTrace({ entries }: { entries: ToolActivityEntry[] }): ReactElement {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="mt-1.5 max-w-[82%]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-[10px] text-slate-500 hover:text-slate-300 font-mono uppercase tracking-wider cursor-pointer transition"
      >
        {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        Show Execution ({entries.length})
      </button>
      {expanded && (
        <div className="mt-2 relative pl-4 border-l border-white/10 space-y-3 max-h-36 overflow-y-auto scrollbar py-1">
          <StepperEntries entries={entries} />
        </div>
      )}
    </div>
  );
}

function TypingDots(): ReactElement {
  const accentColor = useCharlieStore((s) => s.accentColor);
  return (
    <span className="inline-flex gap-1.5 ml-1" aria-label="Charlie is typing">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-1.5 h-1.5 rounded-full animate-bounce"
          style={{
            background: accentColor,
            animationDelay: `${i * 150}ms`,
          }}
        />
      ))}
    </span>
  );
}

interface ChatViewProps {
  messages: { id?: string; role: string; content: string; turnId?: string }[];
  onSend: (text: string) => void;
  onStop?: () => void;
  loading: boolean;
  voiceState?: string;
  toolActivity?: ToolActivityEntry[];
  executionTraces?: Record<string, ToolActivityEntry[]>;

  // Inline dialog handlers
  activeProposal?: RecoveryProposal | null;
  onApproveRecovery?: (id: string) => void;
  onRejectRecovery?: (id: string) => void;

  activeToolApproval?: ToolApprovalRequest | null;
  onApproveTool?: (id: string) => void;
  onRejectTool?: (id: string) => void;
}

export function ChatView({
  messages,
  onSend,
  onStop,
  loading,
  voiceState = "idle",
  toolActivity,
  executionTraces,
  activeProposal,
  onApproveRecovery,
  onRejectRecovery,
  activeToolApproval,
  onApproveTool,
  onRejectTool,
}: ChatViewProps): ReactElement {
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const isSubmittingRef = useRef(false);

  // Grow the textarea with content up to max-h-32 (128px), then let it scroll internally.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 128)}px`;
  }, [input]);
  const connected = useCharlieStore((s) => s.connected);
  const queuedTexts = useCharlieStore((s) => s.queue.texts);
  const accentColor = useCharlieStore((s) => s.accentColor);
  const [stepperExpanded, setStepperExpanded] = useState(true);

  // Smooth scroll into view
  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTo({
        top: el.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [messages, loading, toolActivity, activeProposal, activeToolApproval]);

  const submit = (overrideText?: string): void => {
    if (isSubmittingRef.current) return;
    const text = overrideText !== undefined ? overrideText : input.trim();
    if (!text) return;
    isSubmittingRef.current = true;
    onSend(text);
    if (overrideText === undefined) setInput("");
    setTimeout(() => { isSubmittingRef.current = false; }, 500);
  };

  const accentDim = rgba(accentColor, 0.08);
  const accentBorder = rgba(accentColor, 0.25);

  const startPrompts = [
    { label: "Audit Docker container security", text: "Audit Docker container security. Find running containers and assess their network ports." },
    { label: "Analyze network ports", text: "Analyze local network ports. Run an enumeration scan on open ports." },
    { label: "Read system logs", text: "Retrieve the last 20 system error logs from Charlie and review them." },
    { label: "Search web for news", text: "Search the web for the latest artificial intelligence news of today." }
  ];

  return (
    <section className="flex flex-col h-full overflow-hidden bg-black/40 border border-[var(--color-glass-border)] rounded-2xl">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-glass-border)]">
        <div className="min-w-0">
          <h1 className="font-display text-lg font-semibold text-[var(--color-text-primary)] tracking-wide">
            Console Chat
          </h1>
        </div>
        <span className="flex items-center gap-2 text-xs font-mono uppercase tracking-widest text-slate-400">
          <span
            className={`w-2 h-2 rounded-full ${
              connected ? "bg-cyan-400 animate-pulse" : "bg-red-500"
            }`}
            aria-hidden="true"
          />
          {connected ? "Online" : "Offline"}
        </span>
      </header>

      {/* Messages viewport */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-6 py-5 space-y-4 scrollbar"
      >
        {messages.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center py-10 max-w-lg mx-auto">
            <Sparkles className="w-10 h-10 text-purple-400/40 mb-4" />
            <h3 className="font-display text-base font-semibold text-[var(--color-text-primary)] tracking-wide text-center">
              CHARLIE Engine Ready
            </h3>
            <p className="text-xs text-slate-400 text-center mt-1.5 leading-relaxed">
              How can I assist your local operations today? Select a template below or type a custom command.
            </p>
            
            {/* Quick-Start suggestions */}
            <div className="grid grid-cols-2 gap-3 w-full mt-6">
              {startPrompts.map((p, i) => (
                <button
                  key={i}
                  onClick={() => submit(p.text)}
                  className="p-3 text-left rounded-xl bg-zinc-900/40 border border-[var(--color-glass-border)] transition hover:border-[var(--color-glass-border-hover)] hover:bg-zinc-900/80 group cursor-pointer"
                >
                  <p className="text-xs font-semibold text-slate-300 group-hover:text-slate-100 transition truncate">
                    {p.label}
                  </p>
                  <p className="text-[10px] text-slate-500 truncate mt-1">
                    {p.text}
                  </p>
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => {
          const isUser = m.role === "user";
          const trace = !isUser && m.turnId ? executionTraces?.[m.turnId] : undefined;
          const isQueued = isUser && queuedTexts.includes(m.content);
          return (
            <div
              key={m.id ?? `${m.role}-${i}`}
              className={`flex flex-col ${isUser ? "items-end" : "items-start"} animate-[rise_0.2s_ease-out]`}
            >
              <div
                style={{
                  background: isUser ? accentDim : "var(--color-surface-hover)",
                  borderColor: isUser ? accentBorder : "var(--color-glass-border)",
                  opacity: isQueued ? 0.6 : 1,
                }}
                className={`max-w-[82%] px-4 py-3 rounded-xl border text-[14px] leading-relaxed text-slate-100`}
              >
                {m.content ? formatMessageContent(m.content) : (isUser ? "" : <TypingDots />)}
              </div>
              {isQueued && (
                <span className="text-[10px] text-amber-400 font-mono mt-1 flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                  Queued -- waiting for current reply to finish
                </span>
              )}
              {trace && trace.length > 0 && <MessageExecutionTrace entries={trace} />}
            </div>
          );
        })}

        {/* Inline Recovery Proposal */}
        {activeProposal && onApproveRecovery && onRejectRecovery && (
          <div className="flex justify-start animate-[rise_0.25s_ease-out] my-3">
            <div className="w-full max-w-xl rounded-xl border border-orange-500/35 bg-orange-950/15 backdrop-blur-md p-4">
              <div className="flex items-center gap-2 mb-2 text-orange-400">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                <h4 className="font-display text-xs font-bold uppercase tracking-wider">
                  Command Recovery Proposal
                </h4>
              </div>
              <p className="text-xs text-slate-300 leading-relaxed mb-3">
                Charlie encountered an error running a shell command and has generated a proposed fix.
              </p>
              
              <div className="space-y-2 mb-4 font-mono text-[11px]">
                <div className="p-2.5 rounded bg-red-950/20 border border-red-500/10 text-red-200">
                  <span className="text-[10px] uppercase font-bold text-red-400 block mb-1">Error Command</span>
                  {activeProposal.original_command}
                </div>
                <div className="p-2.5 rounded bg-emerald-950/20 border border-emerald-500/10 text-emerald-200">
                  <span className="text-[10px] uppercase font-bold text-emerald-400 block mb-1">Proposed Fix</span>
                  {activeProposal.proposed_command}
                </div>
                {activeProposal.explanation && (
                  <div className="p-2.5 rounded bg-zinc-900 border border-white/5 text-slate-400">
                    <span className="text-[10px] uppercase font-bold text-slate-400 block mb-1">Rationale</span>
                    {activeProposal.explanation}
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between border-t border-white/5 pt-3">
                <div className="flex items-center gap-1.5">
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      activeProposal.safeguard_passed ? "bg-emerald-400" : "bg-red-400"
                    }`}
                  />
                  <span className="text-[10px] text-slate-500 font-mono">
                    Safeguards: {activeProposal.safeguard_passed ? "PASSED" : "BLOCKED"}
                  </span>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      onRejectRecovery(activeProposal.proposal_id);
                      useCharlieStore.getState().setActiveProposal(null);
                    }}
                    className="px-3 py-1.5 rounded-lg border border-white/10 hover:bg-white/5 text-slate-400 hover:text-slate-100 text-xs font-semibold cursor-pointer transition active:scale-[0.98]"
                  >
                    Decline
                  </button>
                  <button
                    onClick={() => {
                      onApproveRecovery(activeProposal.proposal_id);
                      useCharlieStore.getState().setActiveProposal(null);
                    }}
                    className="px-3 py-1.5 rounded-lg bg-orange-500 text-black text-xs font-semibold cursor-pointer hover:bg-orange-400 transition active:scale-[0.98]"
                  >
                    Approve & Run
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Inline Tool Approval Request */}
        {activeToolApproval && onApproveTool && onRejectTool && (
          <div className="flex justify-start animate-[rise_0.25s_ease-out] my-3">
            <div className="w-full max-w-xl rounded-xl border border-amber-500/35 bg-amber-950/15 backdrop-blur-md p-4">
              <div className="flex items-center gap-2 mb-2 text-amber-400">
                <ShieldAlert className="w-4 h-4 shrink-0" />
                <h4 className="font-display text-xs font-bold uppercase tracking-wider">
                  OS Security Confirmation Required
                </h4>
              </div>
              <p className="text-xs text-slate-300 leading-relaxed mb-3">
                Charlie requested execution of a restricted system tool:
              </p>

              <div className="space-y-2 mb-4 font-mono text-[11px]">
                <div className="p-2.5 rounded bg-zinc-900 border border-white/5 text-slate-200">
                  <span className="text-[10px] uppercase font-bold text-slate-400 block mb-1">Restricted Tool</span>
                  {activeToolApproval.tool_name}
                </div>
                {activeToolApproval.reason && (
                  <div className="p-2.5 rounded bg-zinc-900 border border-white/5 text-amber-300">
                    <span className="text-[10px] uppercase font-bold text-slate-400 block mb-1">Reason</span>
                    {activeToolApproval.reason}
                  </div>
                )}
                {activeToolApproval.arguments && (
                  <div className="p-2.5 rounded bg-zinc-950 border border-white/5 text-cyan-300 whitespace-pre-wrap break-all max-h-32 overflow-y-auto">
                    <span className="text-[10px] uppercase font-bold text-slate-400 block mb-1">Arguments</span>
                    {Object.entries(activeToolApproval.arguments).map(([k, v]) => `${k}: ${String(v)}`).join("\n")}
                  </div>
                )}
              </div>

              <div className="flex justify-end gap-2 border-t border-white/5 pt-3">
                <button
                  onClick={() => {
                    onRejectTool(activeToolApproval.request_id);
                    useCharlieStore.getState().setActiveToolApproval(null);
                  }}
                  className="px-3 py-1.5 rounded-lg border border-white/10 hover:bg-white/5 text-slate-400 hover:text-slate-100 text-xs font-semibold cursor-pointer transition active:scale-[0.98]"
                >
                  Decline
                </button>
                <button
                  onClick={() => {
                    onApproveTool(activeToolApproval.request_id);
                    useCharlieStore.getState().setActiveToolApproval(null);
                  }}
                  className="px-3 py-1.5 rounded-lg bg-amber-500 text-black text-xs font-semibold cursor-pointer hover:bg-amber-400 transition active:scale-[0.98]"
                >
                  Approve & Run
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Floating Stepper Timeline for Tool Activity */}
      {toolActivity && toolActivity.length > 0 && (
        <div className="px-6 pb-2 shrink-0 border-t border-white/5 pt-3 bg-zinc-950/20">
          <div className="flex items-center justify-between text-[10px] text-slate-500 font-mono select-none">
            <span className="flex items-center gap-1.5 font-bold uppercase tracking-wider text-slate-400">
              <Terminal className="w-3.5 h-3.5" />
              Active Stepper Timeline ({toolActivity.length} events)
            </span>
            <button
              onClick={() => setStepperExpanded(!stepperExpanded)}
              className="text-slate-400 hover:text-slate-200 transition cursor-pointer p-0.5 rounded hover:bg-white/5"
            >
              {stepperExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronUp className="w-3.5 h-3.5" />}
            </button>
          </div>
          
          {stepperExpanded && (
            <div className="mt-3 relative pl-4 border-l border-white/10 space-y-3 max-h-36 overflow-y-auto scrollbar py-1">
              <StepperEntries entries={toolActivity} />
            </div>
          )}
        </div>
      )}

      {/* Text Area prompt input */}
      <div className="px-6 py-4 border-t border-[var(--color-glass-border)] shrink-0">
        <div className="flex items-center gap-3 bg-zinc-900/40 rounded-xl border border-[var(--color-glass-border)] px-4 py-2 transition-colors focus-within:border-[var(--color-glass-border-hover)] focus-within:bg-zinc-900/60">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={1}
            placeholder="Ask Charlie anything..."
            aria-label="Ask Charlie anything"
            className="flex-1 bg-transparent resize-none outline-none text-xs text-[var(--color-text-primary)] placeholder:text-slate-500 font-sans py-1 max-h-32 scrollbar"
          />
          {voiceState !== "idle" ? (
            <Button
              variant="danger"
              onClick={onStop}
              aria-label="Stop generation"
              className="shrink-0 px-4 py-1.5 text-[10px] font-bold uppercase tracking-wider"
            >
              Stop
            </Button>
          ) : (
            <Button
              variant="accent"
              onClick={() => submit()}
              disabled={!input.trim() && !loading}
              aria-label="Send message"
              className="shrink-0 px-4 py-1.5 text-[10px] font-bold uppercase tracking-wider"
            >
              Send
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}
