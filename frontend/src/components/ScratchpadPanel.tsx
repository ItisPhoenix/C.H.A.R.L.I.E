"use client";

import { useEffect, useState, useCallback, type ReactElement } from "react";
import { NotebookPen, Plus, Pencil, Trash2, Save, X, RefreshCw } from "lucide-react";
import { useCharlieStore } from "../store/useCharlieStore";
import { Button } from "./Button";

interface ScratchpadEntry {
  index: number;
  text: string;
  created_at: string;
}

export function ScratchpadPanel(): ReactElement {
  const [entries, setEntries] = useState<ScratchpadEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [newText, setNewText] = useState("");
  const [adding, setAdding] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  const [confirmDeleteIndex, setConfirmDeleteIndex] = useState<number | null>(null);

  const fetchEntries = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/scratchpad");
      if (res.ok) {
        setEntries((await res.json()).entries);
      } else {
        useCharlieStore.getState().addAlert({
          severity: "warn",
          message: "Could not load scratchpad -- backend unreachable.",
          timestamp: new Date().toLocaleTimeString(),
        });
      }
    } catch {
      useCharlieStore.getState().addAlert({
        severity: "warn",
        message: "Could not load scratchpad -- backend unreachable.",
        timestamp: new Date().toLocaleTimeString(),
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data fetch on mount
    fetchEntries();
  }, [fetchEntries]);

  const addEntry = async () => {
    const text = newText.trim();
    if (!text) return;
    setAdding(true);
    try {
      const res = await fetch("/api/scratchpad", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (res.ok) {
        setNewText("");
        await fetchEntries();
      } else {
        const body = await res.json().catch(() => ({}));
        useCharlieStore.getState().addAlert({
          severity: "error",
          message: body.error || "Failed to add scratchpad entry.",
          timestamp: new Date().toLocaleTimeString(),
        });
      }
    } catch {
      useCharlieStore.getState().addAlert({
        severity: "error",
        message: "Failed to add scratchpad entry -- backend unreachable.",
        timestamp: new Date().toLocaleTimeString(),
      });
    } finally {
      setAdding(false);
    }
  };

  const startEdit = (entry: ScratchpadEntry) => {
    setEditingIndex(entry.index);
    setEditText(entry.text);
  };

  const cancelEdit = () => {
    setEditingIndex(null);
    setEditText("");
  };

  const saveEdit = async () => {
    if (editingIndex === null) return;
    const text = editText.trim();
    if (!text) return;
    try {
      const res = await fetch(`/api/scratchpad/${editingIndex}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (res.ok) {
        setEditingIndex(null);
        setEditText("");
        await fetchEntries();
      } else {
        useCharlieStore.getState().addAlert({
          severity: "error",
          message: "Failed to save scratchpad entry.",
          timestamp: new Date().toLocaleTimeString(),
        });
      }
    } catch {
      useCharlieStore.getState().addAlert({
        severity: "error",
        message: "Failed to save scratchpad entry -- backend unreachable.",
        timestamp: new Date().toLocaleTimeString(),
      });
    }
  };

  const deleteEntry = async (index: number) => {
    try {
      const res = await fetch(`/api/scratchpad/${index}`, { method: "DELETE" });
      if (res.ok) {
        setConfirmDeleteIndex(null);
        await fetchEntries();
      } else {
        useCharlieStore.getState().addAlert({
          severity: "error",
          message: "Failed to delete scratchpad entry.",
          timestamp: new Date().toLocaleTimeString(),
        });
      }
    } catch {
      useCharlieStore.getState().addAlert({
        severity: "error",
        message: "Failed to delete scratchpad entry -- backend unreachable.",
        timestamp: new Date().toLocaleTimeString(),
      });
    }
  };

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
      <div className="border-b border-white/5 pb-3 flex justify-between items-end">
        <div>
          <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
            <NotebookPen className="w-5 h-5 text-slate-400" />
            Scratchpad
          </h2>
          <p className="text-xs text-slate-500 font-mono mt-1">
            Freeform notes, shared across every session -- updated live by the `scratchpad_*` tools
          </p>
        </div>
        <Button onClick={fetchEntries} className="font-mono">
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-cyan-400" : ""}`} />
          Refresh
        </Button>
      </div>

      <div className="rounded-xl border border-white/5 p-4 bg-zinc-900/20 flex gap-2">
        <input
          type="text"
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !adding) addEntry();
          }}
          placeholder="Add a note..."
          className="flex-1 bg-zinc-950/60 border border-white/10 rounded-lg px-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-500 outline-none focus:border-white/20"
        />
        <Button size="sm" onClick={addEntry} disabled={adding || !newText.trim()}>
          <Plus className="w-3.5 h-3.5" /> Add
        </Button>
      </div>

      {loading ? (
        <p className="text-xs font-mono text-slate-500 animate-pulse py-4">Loading...</p>
      ) : entries.length === 0 ? (
        <p className="text-xs font-mono text-slate-500 italic py-4">Scratchpad is empty.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {entries.map((entry) => {
            const isEditing = editingIndex === entry.index;
            const isConfirmingDelete = confirmDeleteIndex === entry.index;
            return (
              <div
                key={entry.index}
                className="group rounded-xl border border-white/5 p-4 bg-zinc-900/20 space-y-2 flex flex-col"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-slate-500 uppercase font-bold">Entry {entry.index}</span>
                  {!isEditing && (
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition">
                      {isConfirmingDelete ? (
                        <>
                          <button
                            onClick={() => deleteEntry(entry.index)}
                            className="px-2 py-0.5 rounded text-xs font-mono text-red-300 bg-red-500/10 hover:bg-red-500/20 border border-red-500/30"
                          >
                            Confirm delete
                          </button>
                          <button
                            onClick={() => setConfirmDeleteIndex(null)}
                            aria-label="Cancel delete"
                            className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-white/10"
                          >
                            <X className="w-3 h-3" />
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            onClick={() => startEdit(entry)}
                            aria-label={`Edit entry ${entry.index}`}
                            className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-white/10"
                          >
                            <Pencil className="w-3 h-3" />
                          </button>
                          <button
                            onClick={() => setConfirmDeleteIndex(entry.index)}
                            aria-label={`Delete entry ${entry.index}`}
                            className="p-1 rounded text-slate-400 hover:text-red-400 hover:bg-white/10"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </div>

                {isEditing ? (
                  <>
                    <textarea
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      autoFocus
                      spellCheck={false}
                      className="text-xs font-mono text-slate-200 bg-zinc-950/60 border border-white/10 rounded-lg p-2 h-32 resize-none outline-none focus:border-white/20 scrollbar"
                    />
                    <div className="flex gap-2">
                      <Button size="sm" onClick={saveEdit} disabled={!editText.trim()}>
                        <Save className="w-3 h-3" /> Save
                      </Button>
                      <Button size="sm" variant="neutral" onClick={cancelEdit}>
                        Cancel
                      </Button>
                    </div>
                  </>
                ) : (
                  <p className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap max-h-40 overflow-y-auto scrollbar">
                    {entry.text}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
