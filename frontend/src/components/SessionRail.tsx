"use client";

import { useState } from "react";
import type { ReactElement } from "react";
import { useCharlieStore, rgba, lighten } from "@/store/useCharlieStore";

interface SessionItem {
  id: string;
  title: string;
  created_at?: string;
  updated_at?: string;
}

function relativeTime(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return d.toLocaleDateString();
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
  onScopeChange: (target: "all" | "this_launch") => void;
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
                onClick={() => onScopeChange("this_launch")}
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
                onClick={() => onScopeChange("all")}
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

          <div className="flex-1 overflow-y-auto px-3 pb-4 space-y-0.5 scrollbar">
            {filtered.length === 0 && (
              <div className="flex flex-col items-center gap-2 py-8 px-2">
                <svg viewBox="0 0 24 24" className="w-8 h-8 text-[var(--color-text-muted)] opacity-30" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
                <p className="text-xs text-[var(--color-text-muted)] text-center">
                  {query ? "No chats match your search." : "No chats yet. Create one above."}
                </p>
              </div>
            )}
            {filtered.map((s) => {
              const active = s.id === currentId;
              const ts = relativeTime(s.updated_at || s.created_at);
              return (
                <div
                  key={s.id}
                  onClick={() => onSelect(s.id)}
                  style={{
                    background: active ? rgba(accentColor, 0.13) : "transparent",
                    borderColor: active ? accentBorder : "transparent",
                  }}
                  className={`group relative flex items-center gap-2 rounded-xl px-3 py-2.5 cursor-pointer transition border`}
                >
                  {active && (
                    <span
                      style={{ backgroundColor: accentSoft }}
                      className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-4 rounded-full"
                    />
                  )}
                  <div className="min-w-0 flex-1 pl-1">
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
                      <>
                        <p
                          onDoubleClick={() => startEdit(s)}
                          className="text-sm text-[var(--color-text-primary)] truncate leading-tight"
                          style={{ color: active ? accentSoft : undefined }}
                          title={s.title}
                        >
                          {s.title}
                        </p>
                        {ts && (
                          <p className="text-[10px] text-[var(--color-text-muted)] mt-0.5">{ts}</p>
                        )}
                      </>
                    )}
                  </div>
                  {editingId !== s.id && (
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition shrink-0">
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
                        className="rounded-md w-6 h-6 grid place-items-center text-[var(--color-text-muted)] hover:text-red-400 cursor-pointer"
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

          {/* Settings & Accent Color Pickers */}
          <div className="px-5 py-3 border-t border-[var(--color-glass-border)] flex items-center justify-between shrink-0">
            <button
              onClick={() => useCharlieStore.getState().setSettingsOpen(true)}
              className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)] hover:text-white transition cursor-pointer"
            >
              <svg viewBox="0 0 24 24" className="w-4 h-4 animate-[spin_10s_linear_infinite]" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
              <span>Settings</span>
            </button>
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
