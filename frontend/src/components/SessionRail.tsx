"use client";

import { useState, useMemo, type ReactElement } from "react";
import { Plus, Search, MessageSquare, Edit2, Trash2, Download, PanelLeftClose, PanelLeftOpen, X } from "lucide-react";
import { useCharlieStore, rgba } from "../store/useCharlieStore";

interface SessionItem {
  id: string;
  title: string;
  created_at?: string;
  updated_at?: string;
}

interface SessionRailProps {
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  sessions: SessionItem[];
  currentId: string;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onExport: () => void;
  /** "column": fixed-width bordered aside (default). "accordion": fills its
   * parent's width instead, no border/shadow of its own -- for embedding
   * inline inside another bordered container (e.g. a sidebar dropdown). */
  variant?: "column" | "accordion";
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
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function groupSessions(sessions: SessionItem[]) {
  const groups: { [key: string]: SessionItem[] } = {
    Today: [],
    Yesterday: [],
    Earlier: [],
  };

  const now = new Date();
  const todayStr = now.toDateString();
  
  const yesterday = new Date();
  yesterday.setDate(now.getDate() - 1);
  const yesterdayStr = yesterday.toDateString();

  for (const s of sessions) {
    const dateStr = s.updated_at || s.created_at;
    if (!dateStr) {
      groups.Earlier.push(s);
      continue;
    }
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) {
      groups.Earlier.push(s);
      continue;
    }
    const ds = d.toDateString();
    if (ds === todayStr) {
      groups.Today.push(s);
    } else if (ds === yesterdayStr) {
      groups.Yesterday.push(s);
    } else {
      groups.Earlier.push(s);
    }
  }

  return groups;
}

export function SessionRail({
  collapsed = false,
  onToggleCollapse,
  sessions,
  currentId,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  onExport,
  variant = "column",
}: SessionRailProps): ReactElement {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [filterQuery, setFilterQuery] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const accentColor = useCharlieStore((s) => s.accentColor);

  const startRename = (s: SessionItem) => {
    setEditingId(s.id);
    setEditTitle(s.title);
  };

  const saveRename = (id: string) => {
    const trimmed = editTitle.trim();
    if (trimmed) {
      onRename(id, trimmed);
    }
    setEditingId(null);
  };

  const filteredSessions = useMemo(() => {
    if (!filterQuery.trim()) return sessions;
    return sessions.filter((s) =>
      s.title.toLowerCase().includes(filterQuery.toLowerCase())
    );
  }, [sessions, filterQuery]);

  const grouped = useMemo(() => groupSessions(filteredSessions), [filteredSessions]);

  const activeBg = rgba(accentColor, 0.12);
  const activeBorder = rgba(accentColor, 0.35);

  // Render collapsed 56px icon-only rail
  if (collapsed) {
    return (
      <aside className="w-14 shrink-0 border-r border-[var(--color-glass-border)] bg-zinc-950/40 p-2 flex flex-col justify-between items-center select-none font-sans">
        <div className="space-y-4 w-full flex flex-col items-center">
          {onToggleCollapse && (
            <button
              onClick={onToggleCollapse}
              className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-white/5 transition cursor-pointer"
              title="Expand Sessions Rail"
            >
              <PanelLeftOpen className="w-4 h-4" />
            </button>
          )}

          <button
            onClick={onCreate}
            className="w-10 h-10 rounded-xl bg-accent text-black flex items-center justify-center font-bold hover:brightness-110 active:scale-95 transition cursor-pointer shadow-lg"
            title="New Chat"
          >
            <Plus className="w-5 h-5 text-black" />
          </button>

          <div className="w-full space-y-2 pt-2 max-h-[calc(100vh-220px)] overflow-y-auto scrollbar flex flex-col items-center">
            {sessions.map((s) => {
              const active = s.id === currentId;
              return (
                <button
                  key={s.id}
                  onClick={() => onSelect(s.id)}
                  title={s.title}
                  className={`w-9 h-9 rounded-lg flex items-center justify-center cursor-pointer transition ${
                    active ? "bg-white/10 text-cyan-400 border border-cyan-500/40" : "text-slate-500 hover:text-slate-300 hover:bg-white/5"
                  }`}
                >
                  <MessageSquare className="w-4 h-4" />
                </button>
              );
            })}
          </div>
        </div>

        <button
          onClick={onExport}
          className="p-2 rounded-lg text-slate-500 hover:text-slate-300 hover:bg-white/5 transition cursor-pointer"
          title="Export Chat History"
        >
          <Download className="w-4 h-4" />
        </button>
      </aside>
    );
  }

  // Full Expanded 240px Rail (or, in "accordion" variant, an in-flow block
  // that fills its parent's width -- no border/fixed-width of its own since
  // the parent (sidebar dropdown) already provides those).
  return (
    <aside
      className={
        variant === "accordion"
          ? "w-full flex flex-col select-none font-sans"
          : "w-60 shrink-0 border-r border-[var(--color-glass-border)] bg-zinc-950/40 p-4 flex flex-col justify-between select-none font-sans"
      }
    >
      <div className={variant === "accordion" ? "space-y-3 p-3" : "space-y-4"}>
        {/* Top Header Controls */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="font-display text-xs font-bold uppercase tracking-wider text-slate-200">
              Chats
            </h2>
            <span className="text-xs font-mono text-slate-500 bg-zinc-900 border border-white/5 px-1.5 py-0.5 rounded-md font-semibold">
              {sessions.length}
            </span>
          </div>

          <div className="flex items-center gap-1">
            <button
              onClick={onCreate}
              className="p-1.5 rounded-lg bg-zinc-900 border border-white/10 text-slate-200 hover:bg-zinc-800 active:scale-95 transition cursor-pointer"
              title="New Chat"
            >
              <Plus className="w-3.5 h-3.5" />
            </button>
            {onToggleCollapse && (
              <button
                onClick={onToggleCollapse}
                className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/5 transition cursor-pointer"
                title="Collapse Sessions Rail"
              >
                <PanelLeftClose className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>

        {/* Local Search input */}
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 w-3.5 h-3.5 text-slate-500 pointer-events-none" />
          <input
            type="text"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            placeholder="Search sessions..."
            className="w-full bg-zinc-900/60 border border-[var(--color-glass-border)] rounded-lg pl-8 pr-3 py-1.5 text-xs text-[var(--color-text-primary)] placeholder:text-slate-500 outline-none transition focus:border-[var(--color-glass-border-hover)]"
          />
        </div>

        {/* Sessions List Grouped by Time */}
        <div className={`space-y-4 overflow-y-auto pr-1 scrollbar ${variant === "accordion" ? "max-h-72" : "max-h-[calc(100vh-290px)]"}`}>
          {Object.entries(grouped).map(([groupName, groupItems]) => {
            if (groupItems.length === 0) return null;
            return (
              <div key={groupName} className="space-y-1">
                <h3 className="px-2 text-xs font-mono font-bold uppercase tracking-widest text-slate-500">
                  {groupName}
                </h3>

                <div className="space-y-0.5">
                  {groupItems.map((s) => {
                    const active = s.id === currentId;
                    const isEditing = editingId === s.id;

                    return (
                      <div
                        key={s.id}
                        onClick={() => !isEditing && onSelect(s.id)}
                        style={{
                          backgroundColor: active ? activeBg : "transparent",
                          borderColor: active ? activeBorder : "transparent",
                        }}
                        className={`group relative rounded-xl px-3 py-2 text-xs border transition cursor-pointer flex items-center justify-between ${
                          active
                            ? "text-slate-100 font-medium shadow-sm"
                            : "text-slate-400 hover:text-slate-200 hover:bg-white/5"
                        }`}
                      >
                        <div className="flex items-center gap-2 min-w-0 flex-1 pr-2">
                          <MessageSquare
                            className={`w-3.5 h-3.5 shrink-0 ${
                              active ? "text-cyan-400" : "text-slate-500"
                            }`}
                          />

                          {isEditing ? (
                            <input
                              type="text"
                              value={editTitle}
                              onChange={(e) => setEditTitle(e.target.value)}
                              onBlur={() => saveRename(s.id)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") saveRename(s.id);
                                if (e.key === "Escape") setEditingId(null);
                              }}
                              autoFocus
                              className="w-full bg-zinc-900 border border-white/20 rounded px-1.5 py-0.5 text-xs text-slate-100 outline-none"
                            />
                          ) : (
                            <div className="truncate flex-1">
                              <p className="truncate font-medium" title={s.title}>{s.title}</p>
                              <span className="text-xs text-slate-500 font-mono block">
                                {relativeTime(s.updated_at || s.created_at)}
                              </span>
                            </div>
                          )}
                        </div>

                        {/* Edit/Delete actions on hover */}
                        {!isEditing && confirmDeleteId === s.id ? (
                          <div className="flex items-center gap-1 shrink-0">
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                onDelete(s.id);
                                setConfirmDeleteId(null);
                              }}
                              className="px-1.5 py-0.5 rounded text-xs font-mono text-red-300 bg-red-500/10 hover:bg-red-500/20 border border-red-500/30"
                              title={`Confirm delete "${s.title}"`}
                            >
                              Confirm
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                setConfirmDeleteId(null);
                              }}
                              className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-white/10"
                              title="Cancel"
                            >
                              <X className="w-3 h-3" />
                            </button>
                          </div>
                        ) : (
                          !isEditing && (
                            <div className="opacity-0 group-hover:opacity-100 transition flex items-center gap-1 shrink-0">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  startRename(s);
                                }}
                                className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-white/10"
                                title="Rename"
                              >
                                <Edit2 className="w-3 h-3" />
                              </button>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setConfirmDeleteId(s.id);
                                }}
                                className="p-1 rounded text-slate-400 hover:text-red-400 hover:bg-white/10"
                                title="Delete"
                              >
                                <Trash2 className="w-3 h-3" />
                              </button>
                            </div>
                          )
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Export Footer button */}
      <button
        onClick={onExport}
        className={`w-full py-2 px-3 rounded-xl border border-white/5 bg-zinc-900/40 hover:bg-white/5 text-xs font-mono text-slate-400 hover:text-slate-200 transition flex items-center justify-center gap-2 cursor-pointer active:scale-98 ${
          variant === "accordion" ? "mt-1 mx-3 w-[calc(100%-1.5rem)]" : ""
        }`}
      >
        <Download className="w-3.5 h-3.5" />
        Export History
      </button>
    </aside>
  );
}
