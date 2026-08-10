"use client";

import { useEffect, useMemo, useState, type ReactElement } from "react";
import {
  Search, MessageSquare, Cpu, Mic, MicOff, Volume2, VolumeX, Settings, Download, Play, Check,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Session } from "../store/useCharlieStore";

interface CommandPaletteProps {
  onClose: () => void;
  sessions: Session[];
  currentSessionId: string;
  onJumpToSession: (id: string) => void;
  models: string[];
  activeModel: string;
  onSwitchModel: (id: string) => void;
  micMuted: boolean;
  onToggleMic: () => void;
  audioMuted: boolean;
  onToggleAudio: () => void;
  onOpenSettings: () => void;
  onExportHistory: () => void;
  onStartBackgroundTask: (text: string) => void;
}

interface Command {
  id: string;
  label: string;
  hint?: string;
  icon: LucideIcon;
  keywords: string;
  run: () => void;
}

/** Ctrl+K overlay covering the 6 actions execution_plan.md's A4 item lists --
 * a real command surface, not the session-title search box it used to just focus.
 * Parent mounts this only while open (`{paletteOpen && <CommandPalette .../>}`), so
 * a fresh mount is the reset mechanism -- no effect-based state reset needed. */
export function CommandPalette({
  onClose, sessions, currentSessionId, onJumpToSession, models, activeModel,
  onSwitchModel, micMuted, onToggleMic, audioMuted, onToggleAudio, onOpenSettings,
  onExportHistory, onStartBackgroundTask,
}: CommandPaletteProps): ReactElement {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);

  const commands = useMemo<Command[]>(() => {
    const sessionCmds: Command[] = sessions.map((s) => ({
      id: `session:${s.id}`,
      label: s.title || "Untitled session",
      hint: s.id === currentSessionId ? "current" : "jump to session",
      icon: MessageSquare,
      keywords: `session ${s.title}`,
      run: () => onJumpToSession(s.id),
    }));

    const modelCmds: Command[] = models.map((m) => ({
      id: `model:${m}`,
      label: `Switch model: ${m}`,
      hint: m === activeModel ? "active" : undefined,
      icon: Cpu,
      keywords: `model switch ${m}`,
      run: () => onSwitchModel(m),
    }));

    const trimmed = query.trim();
    const taskCmd: Command[] = trimmed
      ? [{
          id: "task:free-text",
          label: `Start background task: "${trimmed}"`,
          icon: Play,
          keywords: "",
          run: () => onStartBackgroundTask(trimmed),
        }]
      : [];

    const staticCmds: Command[] = [
      {
        id: "mic:toggle",
        label: micMuted ? "Unmute microphone" : "Mute microphone",
        icon: micMuted ? MicOff : Mic,
        keywords: "mic microphone mute unmute audio input",
        run: onToggleMic,
      },
      {
        id: "audio:toggle",
        label: audioMuted ? "Unmute audio output" : "Mute audio output",
        icon: audioMuted ? VolumeX : Volume2,
        keywords: "audio speaker volume mute unmute output",
        run: onToggleAudio,
      },
      {
        id: "settings:open",
        label: "Open settings",
        icon: Settings,
        keywords: "settings config preferences",
        run: onOpenSettings,
      },
      {
        id: "history:export",
        label: "Export chat history",
        icon: Download,
        keywords: "export history download json",
        run: onExportHistory,
      },
    ];

    return [...taskCmd, ...staticCmds, ...modelCmds, ...sessionCmds];
  }, [
    sessions, currentSessionId, onJumpToSession, models, activeModel, onSwitchModel,
    micMuted, onToggleMic, audioMuted, onToggleAudio, onOpenSettings, onExportHistory,
    query, onStartBackgroundTask,
  ]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter(
      (c) => c.id === "task:free-text" || `${c.label} ${c.keywords}`.toLowerCase().includes(q)
    );
  }, [commands, query]);

  // Clamp instead of resetting via effect -- avoids cascading setState-in-effect renders.
  const safeActiveIndex = filtered.length === 0 ? 0 : Math.min(activeIndex, filtered.length - 1);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex(Math.min(safeActiveIndex + 1, filtered.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex(Math.max(safeActiveIndex - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const cmd = filtered[safeActiveIndex];
        if (cmd) {
          cmd.run();
          onClose();
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [filtered, safeActiveIndex, onClose]);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center pt-[15vh] bg-black/60"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl bg-zinc-950/95 border border-[var(--color-glass-border)] shadow-2xl animate-[rise_0.15s_ease-out] overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="relative border-b border-[var(--color-glass-border)]">
          <Search className="absolute left-3.5 top-3.5 w-4 h-4 text-slate-500" />
          <input
            autoFocus
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Type a command or search sessions..."
            className="w-full bg-transparent pl-10 pr-4 py-3.5 text-sm text-[var(--color-text-primary)] placeholder:text-slate-500 outline-none"
          />
        </div>
        <div className="max-h-80 overflow-y-auto scrollbar py-1.5">
          {filtered.length === 0 ? (
            <div className="py-6 text-center text-xs font-mono text-slate-500">No matching commands</div>
          ) : (
            filtered.map((cmd, i) => {
              const Icon = cmd.icon;
              return (
                <button
                  key={cmd.id}
                  onClick={() => { cmd.run(); onClose(); }}
                  onMouseEnter={() => setActiveIndex(i)}
                  className={`w-full flex items-center gap-3 px-4 py-2.5 text-left text-xs transition ${
                    i === safeActiveIndex ? "bg-white/5 text-slate-100" : "text-slate-300"
                  }`}
                >
                  <Icon className="w-3.5 h-3.5 shrink-0 text-slate-400" />
                  <span className="flex-1 truncate">{cmd.label}</span>
                  {cmd.hint === "active" || cmd.hint === "current" ? (
                    <Check className="w-3.5 h-3.5 shrink-0 text-cyan-400" />
                  ) : cmd.hint ? (
                    <span className="text-xs font-mono text-slate-500 shrink-0">{cmd.hint}</span>
                  ) : null}
                </button>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
