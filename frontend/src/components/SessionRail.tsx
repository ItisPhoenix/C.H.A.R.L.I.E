"use client";

import { useState } from "react";
import type { ReactElement } from "react";
import { useCharlieStore, rgba, lighten } from "@/store/useCharlieStore";

interface SessionItem {
  id: string;
  title: string;
  created_at?: string;
}

interface SessionRailProps {
  collapsed: boolean;
  onToggle: () => void;
  sessions: SessionItem[];
  currentId: string;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onExport: () => void;
  onScopeChange: () => void;
}

export function SessionRail({
  collapsed,
  onToggle,
  sessions,
  currentId,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  onExport,
  onScopeChange,
}: SessionRailProps): ReactElement {
  const sessionScope = useCharlieStore((s) => s.sessionScope);
  const launchId = useCharlieStore((s) => s.launchId);
  const accentColor = useCharlieStore((s) => s.accentColor);
  const setAccentColor = useCharlieStore((s) => s.setAccentColor);
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const filtered = sessions.filter((s) =>
    s.title.toLowerCase().includes(query.toLowerCase())
  );

  const startEdit = (s: SessionItem): void => {
    setEditingId(s.id);
    setDraft(s.title);
  };

  const commitEdit = (id: string): void => {
    const title = draft.trim();
    if (title) onRename(id, title);
    setEditingId(null);
  };

  const accentDim = rgba(accentColor, 0.12);
  const accentBorder = rgba(accentColor, 0.25);
  const accentSoft = lighten(accentColor, 0.35);

  return (
    <aside
      className={`glass glass-hover anim-left flex flex-col shrink-0 h-full overflow-hidden rounded-2xl ${
        collapsed ? "w-[72px]" : "w-72"
      }`}
    >
      {collapsed ? (
        <div className="flex flex-col items-center gap-2 py-4 px-2 h-full">
          <button
            onClick={onToggle}
            aria-label="Expand chats"
            style={{ background: accentDim, color: accentSoft, borderColor: accentBorder }}
            className="rounded-xl w-10 h-10 grid place-items-center border cursor-pointer transition hover:opacity-80"
          >
            <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 18l6-6-6-6" />
            </svg>
          </button>
          <button
            onClick={onCreate}
            aria-label="New chat"
            style={{ background: accentDim, color: accentSoft, borderColor: accentBorder }}
            className="rounded-xl w-10 h-10 grid place-items-center border cursor-pointer transition hover:opacity-80"
          >
            <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
          <button
            onClick={onExport}
            aria-label="Export history"
            className="rounded-xl w-10 h-10 grid place-items-center text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] cursor-pointer transition"
          >
            <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 3v12M7 10l5 5 5-5M5 21h14" />
            </svg>
          </button>
          <div className="flex-1 w-full mt-2 space-y-2 overflow-y-auto scrollbar">
            {sessions.map((s) => {
              const active = s.id === currentId;
              return (
                <button
                  key={s.id}
                  onClick={() => onSelect(s.id)}
                  aria-label={s.title}
                  title={s.title}
                  style={{
                    background: active ? accentDim : "transparent",
                    color: active ? accentSoft : "#6b7280",
                    borderColor: active ? accentBorder : "transparent",
                  }}
                  className={`w-10 h-10 mx-auto grid place-items-center rounded-xl text-sm font-display transition cursor-pointer border`}
                >
                  {(s.title || "?").charAt(0).toUpperCase()}
                </button>
              );
            })}
          </div>
        </div>
      ) : (
        <>
          <div className="px-5 py-4 flex items-center justify-between">
            <h2 className="font-display text-base font-semibold text-[var(--color-text-primary)]">
              Chats
            </h2>
            <div className="flex items-center gap-1">
              <button
                onClick={onToggle}
                aria-label="Collapse chats"
                className="rounded-xl w-8 h-8 grid place-items-center text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] cursor-pointer transition"
              >
                <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M15 18l-6-6 6-6" />
                </svg>
              </button>
              <button
                onClick={onCreate}
                aria-label="New chat"
                style={{ background: accentDim, color: accentSoft, borderColor: accentBorder }}
                className="rounded-xl w-8 h-8 grid place-items-center border cursor-pointer transition hover:opacity-80"
              >
                <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M12 5v14M5 12h14" />
                </svg>
              </button>
              <button
                onClick={onExport}
                aria-label="Export history"
                className="rounded-xl w-8 h-8 grid place-items-center text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] cursor-pointer transition"
              >
                <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 3v12M7 10l5 5 5-5M5 21h14" />
                </svg>
              </button>
            </div>
          </div>

          <div className="px-4 pb-3">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search chats..."
              aria-label="Search chats"
              className="w-full rounded-xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] px-3 py-2 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] outline-none focus:border-[var(--color-accent-teal)]/40 transition"
              style={{
                borderColor: query ? accentBorder : undefined,
              }}
            />
          </div>

          {launchId && (
            <div className="px-4 pb-3">
              <div className="flex rounded-xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] p-0.5 text-xs">
                <button
                  onClick={() => {
                    if (sessionScope !== "this_launch") onScopeChange();
                  }}
                  aria-pressed={sessionScope === "this_launch"}
                  style={{
                    background: sessionScope === "this_launch" ? accentColor : "transparent",
                    color: sessionScope === "this_launch" ? "#03151a" : "#6b7280",
                  }}
                  className={`flex-1 rounded-lg py-1.5 font-medium cursor-pointer transition`}
                >
                  This Launch
                </button>
                <button
                  onClick={() => {
                    if (sessionScope !== "all") onScopeChange();
                  }}
                  aria-pressed={sessionScope === "all"}
                  style={{
                    background: sessionScope === "all" ? accentColor : "transparent",
                    color: sessionScope === "all" ? "#03151a" : "#6b7280",
                  }}
                  className={`flex-1 rounded-lg py-1.5 font-medium cursor-pointer transition`}
                >
                  All
                </button>
              </div>
            </div>
          )}

          <div className="flex-1 overflow-y-auto px-3 pb-4 space-y-1 scrollbar">
            {filtered.length === 0 && (
              <p className="text-xs text-[var(--color-text-muted)] px-2 py-3">
                No chats yet.
              </p>
            )}
            {filtered.map((s) => {
              const active = s.id === currentId;
              return (
                <div
                  key={s.id}
                  onClick={() => onSelect(s.id)}
                  style={{
                    background: active ? rgba(accentColor, 0.1) : "transparent",
                    borderColor: active ? accentBorder : "transparent",
                  }}
                  className={`group flex items-center gap-2 rounded-xl px-3 py-2.5 cursor-pointer transition border`}
                >
                  <div className="min-w-0 flex-1">
                    {editingId === s.id ? (
                      <input
                        autoFocus
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        onBlur={() => commitEdit(s.id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") commitEdit(s.id);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        style={{ borderBottomColor: accentBorder }}
                        className="w-full bg-transparent outline-none text-sm text-[var(--color-text-primary)] border-b"
                      />
                    ) : (
                      <p
                        onDoubleClick={() => startEdit(s)}
                        className="text-sm text-[var(--color-text-primary)] truncate"
                        title={s.title}
                      >
                        {s.title}
                      </p>
                    )}
                  </div>
                  {editingId !== s.id && (
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition">
                      <button
                        aria-label="Rename chat"
                        onClick={(e) => {
                          e.stopPropagation();
                          startEdit(s);
                        }}
                        className="rounded-md w-6 h-6 grid place-items-center text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] cursor-pointer"
                      >
                        <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
                        </svg>
                      </button>
                      <button
                        aria-label="Delete chat"
                        onClick={(e) => {
                          e.stopPropagation();
                          onDelete(s.id);
                        }}
                        className="rounded-md w-6 h-6 grid place-items-center text-[var(--color-text-muted)] hover:text-status-error cursor-pointer"
                      >
                        <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" />
                        </svg>
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Accent Color Settings Picker */}
          <div className="px-5 py-3 border-t border-[var(--color-glass-border)] flex items-center justify-between shrink-0">
            <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-mono">Accent</span>
            <div className="flex gap-1.5">
              {["#a855f7", "#3b82f6", "#ef4444", "#f59e0b", "#06b6d4"].map((color) => (
                <button
                  key={color}
                  onClick={() => setAccentColor(color)}
                  className="w-3.5 h-3.5 rounded-full border border-white/20 transition hover:scale-110 cursor-pointer"
                  style={{
                    background: color,
                    outline: accentColor === color ? `1.5px solid ${lighten(color, 0.35)}` : "none",
                    outlineOffset: "1px",
                  }}
                  aria-label={`Set accent to ${color}`}
                />
              ))}
            </div>
          </div>
        </>
      )}
    </aside>
  );
}
