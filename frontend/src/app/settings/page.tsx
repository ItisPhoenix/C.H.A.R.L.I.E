"use client";

import { useCallback, useEffect, useMemo, useState, type ReactElement } from "react";
import Link from "next/link";
import { ArrowLeft, Save, RefreshCw, Search } from "lucide-react";

interface FieldSpec {
  key: string;
  group: string;
  label: string;
  type: "bool" | "int" | "float" | "str" | "list";
  secret: boolean;
  restart: "voice" | "mcp" | "plugins" | "process" | "reload" | null;
  value: unknown;
  is_set: boolean | null;
}

type SaveState = "idle" | "saved" | "error";

const WIDE_TYPES = new Set(["list"]);
const WIDE_KEYS = new Set([
  "LLM_URL",
  "VISION_LLM_URL",
  "MEMORY_EMBEDDING_URL",
  "SEARXNG_URL",
  "WAKE_WORD_MODEL_PATH",
  "WAKE_WORD_CHIME_PATH",
  "MCP_CONFIG_PATH",
  "LLM_MODEL",
  "MEMORY_EMBEDDING_MODEL",
]);

const GROUP_ORDER = [
  "LLM",
  "Voice & Speech",
  "VAD & ASR Tuning",
  "Chat Behavior",
  "Memory Files",
  "Search Providers",
  "Wake Word",
  "Vector Memory",
  "Agentic OS",
  "Desktop Control",
  "Vision",
  "Plugins",
  "Server",
];

const GROUP_HELP: Record<string, string> = {
  "LLM": "Primary and fallback model endpoints.",
  "Voice & Speech": "Microphone device, ASR model, and TTS voice.",
  "VAD & ASR Tuning": "Speech-detection thresholds and transcription accuracy.",
  "Chat Behavior": "Tool calling, context window, and history compression.",
  "Memory Files": "MEMORY.md / USER.md / OPINIONS.md and session history storage.",
  "Search Providers": "Web search fallback chain.",
  "Wake Word": "Hands-free “Charlie” activation.",
  "Vector Memory": "Semantic recall and the knowledge graph.",
  "Agentic OS": "Swarm orchestration and MCP servers.",
  "Desktop Control": "Screen perception and UI automation.",
  "Vision": "Separate vision-model endpoint for screenshots.",
  "Plugins": "Hybrid plugin sandbox.",
  "Server": "Bind address and settings that need a full restart.",
};

const RESTART_META: Record<string, { label: string; color: string; bg: string }> = {
  voice: { label: "Voice · Reload", color: "var(--color-accent-teal, #06b6d4)", bg: "var(--color-accent-teal-dim)" },
  mcp: { label: "MCP · Reload", color: "var(--color-accent-teal, #06b6d4)", bg: "var(--color-accent-teal-dim)" },
  plugins: { label: "Plugins · Reload", color: "var(--color-accent-teal, #06b6d4)", bg: "var(--color-accent-teal-dim)" },
  reload: { label: "Reload", color: "var(--color-accent-teal, #06b6d4)", bg: "var(--color-accent-teal-dim)" },
  process: { label: "Needs restart", color: "var(--color-status-warning)", bg: "var(--color-status-warning-dim)" },
};

const SELECT_OPTIONS: Record<string, { value: string; label: string }[]> = {
  GPU_DEVICE: [
    { value: "cuda", label: "cuda (GPU acceleration)" },
    { value: "cpu", label: "cpu" },
  ],
  WHISPER_MODEL: [
    { value: "large-v3", label: "large-v3 (best accuracy)" },
    { value: "distil-large-v3", label: "distil-large-v3 (faster)" },
    { value: "medium", label: "medium" },
    { value: "small", label: "small" },
    { value: "base", label: "base" },
  ],
  // All English-language Kokoro voices (lang prefix a=American, b=British).
  KOKORO_VOICE: [
    { value: "af_heart", label: "af_heart (American female, default)" },
    { value: "af_alloy", label: "af_alloy (American female)" },
    { value: "af_aoede", label: "af_aoede (American female)" },
    { value: "af_bella", label: "af_bella (American female)" },
    { value: "af_jessica", label: "af_jessica (American female)" },
    { value: "af_kore", label: "af_kore (American female)" },
    { value: "af_nicole", label: "af_nicole (American female)" },
    { value: "af_nova", label: "af_nova (American female)" },
    { value: "af_river", label: "af_river (American female)" },
    { value: "af_sarah", label: "af_sarah (American female)" },
    { value: "af_sky", label: "af_sky (American female)" },
    { value: "am_adam", label: "am_adam (American male)" },
    { value: "am_echo", label: "am_echo (American male)" },
    { value: "am_eric", label: "am_eric (American male)" },
    { value: "am_fenrir", label: "am_fenrir (American male)" },
    { value: "am_liam", label: "am_liam (American male)" },
    { value: "am_michael", label: "am_michael (American male)" },
    { value: "am_onyx", label: "am_onyx (American male)" },
    { value: "am_puck", label: "am_puck (American male)" },
    { value: "am_santa", label: "am_santa (American male)" },
    { value: "bf_alice", label: "bf_alice (British female)" },
    { value: "bf_emma", label: "bf_emma (British female)" },
    { value: "bf_isabella", label: "bf_isabella (British female)" },
    { value: "bf_lily", label: "bf_lily (British female)" },
    { value: "bm_daniel", label: "bm_daniel (British male)" },
    { value: "bm_fable", label: "bm_fable (British male)" },
    { value: "bm_george", label: "bm_george (British male)" },
    { value: "bm_lewis", label: "bm_lewis (British male)" },
  ],
};

function initialLocalValue(spec: FieldSpec): string | boolean {
  if (spec.type === "bool") return Boolean(spec.value);
  if (spec.type === "list") return Array.isArray(spec.value) ? spec.value.join("\n") : "";
  if (spec.secret) return "";
  return spec.value === null || spec.value === undefined ? "" : String(spec.value);
}

function isWide(spec: FieldSpec): boolean {
  return WIDE_TYPES.has(spec.type) || WIDE_KEYS.has(spec.key) || spec.secret;
}

export default function SettingsPage(): ReactElement {
  const [fields, setFields] = useState<FieldSpec[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [reloadingEngine, setReloadingEngine] = useState(false);
  const [reloadStatus, setReloadStatus] = useState<"idle" | "done" | "error">("idle");
  const [query, setQuery] = useState("");
  const [localValues, setLocalValues] = useState<Record<string, string | boolean>>({});
  const [saveState, setSaveState] = useState<Record<string, SaveState>>({});
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(new Set());

  const applySpecs = useCallback((specs: FieldSpec[]) => {
    setFields(specs);
    const initial: Record<string, string | boolean> = {};
    for (const spec of specs) initial[spec.key] = initialLocalValue(spec);
    setLocalValues(initial);
  }, []);

  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json())
      .then((data: { fields: FieldSpec[] }) => applySpecs(data.fields || []))
      .catch((e) => console.error("Failed to load settings", e))
      .finally(() => setLoading(false));
  }, [applySpecs]);

  const fieldsByKey = useMemo(() => {
    const map = new Map<string, FieldSpec>();
    for (const f of fields) map.set(f.key, f);
    return map;
  }, [fields]);

  const handleSaveAll = async () => {
    if (dirtyKeys.size === 0) return;
    const payload: Record<string, unknown> = {};
    for (const key of dirtyKeys) {
      const spec = fieldsByKey.get(key);
      if (!spec) continue;
      const raw = localValues[key];
      if (spec.secret && raw === "") continue;
      if (spec.type === "list") {
        payload[key] = String(raw).split("\n").map((s) => s.trim()).filter(Boolean);
      } else if (spec.type === "int") {
        payload[key] = raw === "" ? 0 : parseInt(String(raw), 10);
      } else if (spec.type === "float") {
        payload[key] = raw === "" ? 0 : parseFloat(String(raw));
      } else {
        payload[key] = raw;
      }
    }

    setSaving(true);
    const savedKeys = Object.keys(payload);
    try {
      const r = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const res = await r.json();
      const nextState: Record<string, SaveState> = {};
      for (const key of savedKeys) nextState[key] = res.status === "ok" ? "saved" : "error";
      setSaveState((s) => ({ ...s, ...nextState }));
      if (res.status === "ok") {
        setDirtyKeys(new Set());
      }
      setTimeout(() => {
        setSaveState((s) => {
          const next = { ...s };
          for (const key of savedKeys) delete next[key];
          return next;
        });
      }, 2000);
    } catch (e) {
      console.error("Failed to save settings", e);
    } finally {
      setSaving(false);
    }
  };

  const handleReload = async () => {
    setReloadingEngine(true);
    setReloadStatus("idle");
    try {
      const r = await fetch("/api/config/reload", { method: "POST" });
      const res = await r.json();
      setReloadStatus(res.status === "ok" ? "done" : "error");
    } catch (e) {
      console.error("Failed to reload engine", e);
      setReloadStatus("error");
    } finally {
      setReloadingEngine(false);
      setTimeout(() => setReloadStatus("idle"), 2500);
    }
  };

  const handleChange = (spec: FieldSpec, value: string | boolean) => {
    setLocalValues((s) => ({ ...s, [spec.key]: value }));
    setDirtyKeys((s) => new Set(s).add(spec.key));
  };

  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? fields.filter((f) => f.label.toLowerCase().includes(q) || f.key.toLowerCase().includes(q))
      : fields;
    const map = new Map<string, FieldSpec[]>();
    for (const f of filtered) {
      if (!map.has(f.group)) map.set(f.group, []);
      map.get(f.group)!.push(f);
    }
    return map;
  }, [fields, query]);

  const visibleGroups = GROUP_ORDER.filter((g) => (grouped.get(g)?.length ?? 0) > 0);
  const hasPending = dirtyKeys.size > 0;

  const scrollToGroup = (group: string) => {
    const el = document.getElementById(`group-${group.replace(/\s+/g, "-")}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  return (
    <div className="h-full w-full bg-black text-[var(--color-text-primary)] flex flex-col overflow-hidden font-sans">
      
      {/* Header bar */}
      <header className="px-8 py-4 border-b border-[var(--color-glass-border)] bg-zinc-950/80 backdrop-blur-md flex items-center justify-between shrink-0 select-none">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="rounded-lg w-8 h-8 grid place-items-center text-slate-400 hover:text-slate-100 hover:bg-white/5 active:scale-95 transition"
            aria-label="Back to chat"
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div>
            <h1 className="font-display text-lg font-bold uppercase tracking-wide">
              Settings Config
            </h1>
            <p className="text-xs text-slate-500 font-mono">
              Charlie Engine Properties Editor
            </p>
          </div>
        </div>

        {/* Global actions */}
        <div className="flex items-center gap-3">
          <span className="text-xs font-mono text-slate-500">
            {hasPending ? `${dirtyKeys.size} unsaved` : "No modifications"}
          </span>
          <button
            onClick={() => void handleSaveAll()}
            disabled={!hasPending || saving}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-black text-xs font-bold uppercase disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition active:scale-[0.98]"
          >
            <Save className="w-3.5 h-3.5" />
            {saving ? "Saving..." : "Save"}
          </button>
          <button
            onClick={() => void handleReload()}
            disabled={reloadingEngine}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-slate-200 text-xs font-bold uppercase disabled:opacity-60 cursor-pointer transition active:scale-[0.98]"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${reloadingEngine ? "animate-spin" : ""}`} />
            {reloadingEngine ? "Reloading..." : reloadStatus === "done" ? "Reloaded" : reloadStatus === "error" ? "Reload failed" : "Reload"}
          </button>
        </div>
      </header>

      {/* Main settings area */}
      <div className="flex-1 flex overflow-hidden">
        
        {/* Sticky sidebar category navigator */}
        <nav className="w-60 border-r border-[var(--color-glass-border)] bg-zinc-950/20 p-4 shrink-0 flex flex-col gap-1 overflow-y-auto scrollbar select-none">
          <div className="relative flex items-center mb-3">
            <Search className="absolute left-2.5 w-3.5 h-3.5 text-slate-500" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search properties..."
              className="w-full rounded-lg bg-zinc-900/60 border border-[var(--color-glass-border)] pl-8 pr-2.5 py-1.5 text-xs text-[var(--color-text-primary)] placeholder:text-slate-500 outline-none transition focus:border-[var(--color-glass-border-hover)]"
            />
          </div>

          <h2 className="px-2 text-xs font-bold text-slate-500 uppercase tracking-widest font-mono mb-2">
            Categories
          </h2>

          <div className="space-y-0.5">
            {visibleGroups.map((group) => (
              <button
                key={group}
                onClick={() => scrollToGroup(group)}
                className="w-full text-left rounded-lg px-2.5 py-2 text-xs text-slate-400 hover:text-slate-100 hover:bg-white/5 active:scale-[0.98] transition truncate cursor-pointer font-medium"
              >
                {group}
              </button>
            ))}
          </div>
        </nav>

        {/* Scrollable pane of settings sections */}
        <div className="flex-1 overflow-y-auto p-8 scrollbar space-y-8 bg-zinc-950/10">
          {loading ? (
            <div className="h-full flex flex-col items-center justify-center gap-3">
              <RefreshCw className="w-6 h-6 text-slate-600 animate-spin" />
              <p className="text-xs text-slate-500 font-mono">Querying properties spec...</p>
            </div>
          ) : visibleGroups.length === 0 ? (
            <p className="text-xs text-slate-500 font-mono text-center py-12">
              No matching properties found.
            </p>
          ) : (
            visibleGroups.map((group) => (
              <section
                key={group}
                id={`group-${group.replace(/\s+/g, "-")}`}
                className="scroll-mt-6 space-y-4"
              >
                {/* Category Header */}
                <div className="border-b border-white/5 pb-2">
                  <h2 className="font-display text-lg font-bold text-slate-100 uppercase tracking-wide">
                    {group}
                  </h2>
                  <p className="text-xs text-slate-500 mt-1 leading-relaxed">
                    {GROUP_HELP[group]}
                  </p>
                </div>

                {/* Form fields Grid */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {grouped.get(group)!.map((spec) => (
                    <FieldRow
                      key={spec.key}
                      spec={spec}
                      value={localValues[spec.key]}
                      saveState={saveState[spec.key] ?? "idle"}
                      wide={isWide(spec)}
                      onChange={(v) => handleChange(spec, v)}
                    />
                  ))}
                </div>
              </section>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function FieldRow({
  spec,
  value,
  saveState,
  wide,
  onChange,
}: {
  spec: FieldSpec;
  value: string | boolean | undefined;
  saveState: SaveState;
  wide: boolean;
  onChange: (value: string | boolean) => void;
}): ReactElement {
  const restartMeta = spec.restart ? RESTART_META[spec.restart] : null;
  const options = SELECT_OPTIONS[spec.key];

  return (
    <div
      className={`${
        wide ? "col-span-1 md:col-span-2" : "col-span-1"
      } flex flex-col justify-between p-4 bg-zinc-900/30 border border-[var(--color-glass-border)] rounded-xl transition hover:border-[var(--color-glass-border-hover)] min-h-[110px]`}
    >
      <div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-semibold text-slate-200">{spec.label}</span>
          {saveState === "saved" && <span className="text-xs font-mono text-emerald-400 uppercase font-bold">Saved</span>}
          {saveState === "error" && <span className="text-xs font-mono text-red-400 uppercase font-bold">Failed</span>}
        </div>
        <div className="flex items-center gap-1.5 flex-wrap mt-1">
          <span className="text-xs font-mono text-slate-500 uppercase">{spec.key}</span>
          {restartMeta && (
            <span
              className="text-xs font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-full"
              style={{ color: restartMeta.color, background: restartMeta.bg }}
            >
              {restartMeta.label}
            </span>
          )}
        </div>
      </div>

      <div className="mt-4">
        {spec.type === "bool" ? (
          <label className="inline-flex items-center gap-2.5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={Boolean(value)}
              onChange={(e) => onChange(e.target.checked)}
              className="w-4 h-4 rounded border-white/10 bg-zinc-950 checked:bg-cyan-500 cursor-pointer"
            />
            <span className="text-xs text-slate-400 font-medium">
              {Boolean(value) ? "Enabled" : "Disabled"}
            </span>
          </label>
        ) : options ? (
          <select
            value={String(value ?? "")}
            onChange={(e) => onChange(e.target.value)}
            className="w-full bg-zinc-950 border border-white/10 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-white/20 transition cursor-pointer"
          >
            {options.map((o) => (
              <option key={o.value} value={o.value} className="bg-zinc-950 text-slate-200">
                {o.label}
              </option>
            ))}
          </select>
        ) : spec.type === "list" ? (
          <textarea
            value={String(value ?? "")}
            onChange={(e) => onChange(e.target.value)}
            placeholder="One entry per line"
            rows={2}
            className="w-full bg-zinc-950 border border-white/10 rounded-lg px-3 py-1.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-white/20 transition resize-y scrollbar"
          />
        ) : (
          <input
            type={spec.secret ? "password" : spec.type === "int" || spec.type === "float" ? "number" : "text"}
            value={String(value ?? "")}
            onChange={(e) => onChange(e.target.value)}
            placeholder={spec.secret ? (spec.is_set ? "•••••••• (click to change)" : "Not configured") : ""}
            step={spec.type === "float" ? "any" : undefined}
            className="w-full bg-zinc-950 border border-white/10 rounded-lg px-3 py-1.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-white/20 transition"
          />
        )}
      </div>
    </div>
  );
}
