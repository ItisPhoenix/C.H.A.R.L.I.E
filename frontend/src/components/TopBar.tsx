"use client";

import { type ReactElement } from "react";
import { Bell, Check, Menu, Mic, MicOff, RefreshCw, Search, Shield, X } from "lucide-react";
import { useCharlieStore, type Alert, type MicState } from "../store/useCharlieStore";

interface TopBarProps {
  mobileMenuOpen: boolean;
  onToggleMobileMenu: () => void;
  activeModel: string;
  modelOpen: boolean;
  onToggleModelOpen: () => void;
  reloadingModel: boolean;
  modelSearchQuery: string;
  onModelSearchChange: (q: string) => void;
  filteredModels: string[];
  onSelectModel: (modelId: string) => void;
  mic: MicState;
  onToggleMic: () => void;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  bellOpen: boolean;
  onToggleBell: () => void;
  alerts: Alert[];
}

/** Top navigation bar: logo, model selector, mic capsule, global search, alerts bell.
 * Extracted out of page.tsx (was ~150 lines inline) so page.tsx only owns layout/routing. */
export function TopBar(props: TopBarProps): ReactElement {
  const {
    mobileMenuOpen, onToggleMobileMenu, activeModel, modelOpen, onToggleModelOpen,
    reloadingModel, modelSearchQuery, onModelSearchChange, filteredModels, onSelectModel,
    mic, onToggleMic, searchQuery, onSearchChange, bellOpen, onToggleBell, alerts,
  } = props;

  return (
    <header className="px-6 py-3 bg-zinc-950/80 border-b border-[var(--color-glass-border)] flex items-center justify-between z-30 shrink-0 select-none">
      <div className="flex items-center gap-6">
        <button
          onClick={onToggleMobileMenu}
          className="md:hidden p-1.5 rounded-lg border border-[var(--color-glass-border)] text-slate-300 hover:text-slate-100 hover:bg-zinc-900 transition active:scale-[0.98] cursor-pointer"
          aria-label={mobileMenuOpen ? "Close session menu" : "Open session menu"}
        >
          {mobileMenuOpen ? <X className="w-4 h-4" /> : <Menu className="w-4 h-4" />}
        </button>
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-lg bg-accent flex items-center justify-center font-display text-black font-extrabold text-sm shadow-[0_0_12px_var(--accent-border)]">
            C
          </div>
          <div>
            <h1 className="font-display font-bold uppercase tracking-wider text-sm">
              CHARLIE
            </h1>
            <p className="text-xs font-mono text-slate-500 tracking-widest uppercase">
              AI OS dashboard
            </p>
          </div>
        </div>

        {/* Active Model Selector */}
        <div className="relative">
          <button
            onClick={onToggleModelOpen}
            disabled={reloadingModel}
            aria-haspopup="listbox"
            aria-expanded={modelOpen}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--color-glass-border)] bg-zinc-900/40 text-xs font-semibold text-slate-300 hover:text-slate-100 hover:bg-zinc-900 transition active:scale-[0.98] cursor-pointer"
          >
            {reloadingModel ? (
              <RefreshCw className="w-3.5 h-3.5 text-status-listening animate-spin" />
            ) : (
              <Shield className="w-3.5 h-3.5 text-slate-400" />
            )}
            <span className="font-mono text-xs truncate max-w-[120px]">{activeModel}</span>
          </button>

          {modelOpen && (
            <div className="absolute top-9 left-0 z-50 w-64 rounded-xl bg-zinc-950/95 border border-[var(--color-glass-border)] p-2 shadow-2xl animate-[rise_0.15s_ease-out] space-y-1.5">
              <div className="relative px-1">
                <Search className="absolute left-3 top-2.5 w-3 h-3 text-slate-500" />
                <input
                  type="text"
                  value={modelSearchQuery}
                  onChange={(e) => onModelSearchChange(e.target.value)}
                  placeholder="Filter API key / local models..."
                  className="w-full bg-zinc-900 border border-white/10 rounded-md pl-7 pr-2 py-1 text-xs text-slate-200 placeholder:text-slate-500 outline-none font-mono"
                  autoFocus
                />
              </div>
              <div className="max-h-60 overflow-y-auto scrollbar space-y-0.5">
                {filteredModels.length === 0 ? (
                  <div className="py-3 text-center text-xs font-mono text-slate-500">No models match &quot;{modelSearchQuery}&quot;</div>
                ) : (
                  filteredModels.map((model) => (
                    <button
                      key={model}
                      onClick={() => onSelectModel(model)}
                      className="w-full text-left font-mono text-xs text-slate-300 hover:text-slate-100 px-2.5 py-1.5 rounded-lg hover:bg-white/5 flex items-center justify-between cursor-pointer"
                    >
                      <span className="truncate pr-2">{model}</span>
                      {activeModel === model && <Check className="w-3.5 h-3.5 text-status-listening shrink-0" />}
                    </button>
                  ))
                )}
              </div>
            </div>
          )}
        </div>

        {/* Microphone VAD capsule */}
        <button
          onClick={onToggleMic}
          aria-pressed={mic.mic_muted}
          aria-label={mic.mic_muted ? "Unmute microphone" : "Mute microphone"}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-[var(--color-glass-border)] bg-zinc-900/40 text-xs font-semibold text-slate-300 hover:text-slate-100 transition active:scale-[0.98]"
        >
          {mic.mic_muted ? (
            <>
              <MicOff className="w-3.5 h-3.5 text-slate-500" />
              <span className="text-xs text-slate-500 font-mono">MUTED</span>
            </>
          ) : (
            <>
              <Mic className="w-3.5 h-3.5 text-status-listening animate-pulse" />
              <span className="text-xs text-status-listening font-mono">LISTENING</span>
            </>
          )}
        </button>
      </div>

      {/* Global Search & Notifications */}
      <div className="flex items-center gap-4">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 w-3.5 h-3.5 text-slate-500 pointer-events-none" />
          <input
            id="global-search-input"
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Filter sessions"
            className="w-56 bg-zinc-900/60 border border-[var(--color-glass-border)] rounded-lg pl-8 pr-3 py-1.5 text-xs text-[var(--color-text-primary)] placeholder:text-slate-500 outline-none transition focus:border-[var(--color-glass-border-hover)]"
          />
        </div>

        {/* Notification Bell */}
        <div className="relative select-none">
          <button
            onClick={onToggleBell}
            className="relative rounded-lg w-8 h-8 grid place-items-center text-slate-400 hover:text-slate-100 hover:bg-white/5 active:scale-95 transition"
            aria-label="System Alerts"
            aria-haspopup="true"
            aria-expanded={bellOpen}
          >
            <Bell className="w-4 h-4" />
            {alerts.length > 0 && (
              <span className="absolute top-1 right-1.5 w-2 h-2 rounded-full bg-status-listening" />
            )}
          </button>

          {bellOpen && (
            <div className="absolute top-9 right-0 z-50 w-72 rounded-xl bg-zinc-950 border border-[var(--color-glass-border)] p-3 shadow-2xl animate-[rise_0.15s_ease-out] font-mono text-xs">
              <div className="flex items-center justify-between border-b border-white/5 pb-2 mb-2">
                <span className="font-bold text-slate-400 uppercase tracking-widest">Recent Alerts</span>
                <button
                  onClick={() => useCharlieStore.setState({ alerts: [] })}
                  className="text-slate-500 hover:text-status-error hover:bg-white/5 p-0.5 rounded"
                >
                  Clear
                </button>
              </div>
              {alerts.length === 0 ? (
                <p className="text-slate-500 italic py-4 text-center">No alerts logged.</p>
              ) : (
                <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1 scrollbar">
                  {alerts.map((alert, i) => (
                    <div key={i} className="p-1.5 bg-zinc-900 border border-white/5 rounded">
                      <p className={`font-semibold ${alert.severity === "error" ? "text-status-error" : "text-slate-300"}`}>
                        {alert.message}
                      </p>
                      <span className="text-xs text-slate-500 block mt-1">{alert.timestamp}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
