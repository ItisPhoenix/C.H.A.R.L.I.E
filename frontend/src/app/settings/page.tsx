"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactElement } from "react";
import Link from "next/link";

interface FieldSpec {
  key: string;
  group: string;
  label: string;
  type: "bool" | "int" | "float" | "str" | "list";
  secret: boolean;
  restart: "voice" | "mcp" | "plugins" | "process" | null;
  value: unknown;
  is_set: boolean | null;
}

type SaveState = "idle" | "saved" | "error";

// Fields whose value is long-form (multi-line lists or full URLs) get the
// full card width; short scalars (numbers, toggles, short strings) pair up
// two-to-a-row so cards stay compact instead of one long vertical list.
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
  voice: { label: "Voice · via Reload", color: "var(--color-accent-teal)", bg: "var(--color-accent-teal-dim)" },
  mcp: { label: "MCP · via Reload", color: "var(--color-accent-teal)", bg: "var(--color-accent-teal-dim)" },
  plugins: { label: "Plugins · via Reload", color: "var(--color-accent-teal)", bg: "var(--color-accent-teal-dim)" },
  process: { label: "Needs full restart", color: "var(--color-status-warning)", bg: "var(--color-status-warning-dim)" },
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
  KOKORO_VOICE: [
    { value: "af_heart", label: "af_heart (warm female, default)" },
    { value: "af_bella", label: "af_bella" },
    { value: "af_nicole", label: "af_nicole" },
    { value: "am_adam", label: "am_adam (warm male)" },
    { value: "am_michael", label: "am_michael" },
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

  // Only ever called from the Save button -- typing/toggling a field just
  // updates local state below, nothing reaches the network until then.
  const handleSaveAll = async () => {
    if (dirtyKeys.size === 0) return;
    const payload: Record<string, unknown> = {};
    for (const key of dirtyKeys) {
      const spec = fieldsByKey.get(key);
      if (!spec) continue;
      const raw = localValues[key];
      if (spec.secret && raw === "") continue; // blank means "leave the existing secret alone"
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

  // The only path that ever touches the running engine -- applies whatever
  // is currently in .env (i.e. whatever the last Save wrote).
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

  return (
    <div className="h-full w-full overflow-y-auto scrollbar">
      <div className="max-w-[1400px] mx-auto px-8 py-8">
        {/* Sticky header: identity + actions stay reachable while the grid scrolls */}
        <div className="sticky top-0 z-10 -mx-8 px-8 pb-5 pt-1 bg-[var(--color-canvas)]/90 backdrop-blur-md">
          <Link
            href="/"
            className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition inline-flex items-center gap-1.5"
          >
            <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M15 18l-6-6 6-6" />
            </svg>
            Back to Charlie
          </Link>

          <div className="mt-3 flex items-start justify-between gap-6 flex-wrap">
            <div className="max-w-2xl">
              <h1 className="font-display text-3xl font-semibold text-[var(--color-text-primary)]">Settings</h1>
              <p className="text-sm text-[var(--color-text-secondary)] mt-2 leading-relaxed">
                Nothing applies automatically. Edit any field, click <span className="font-medium text-[var(--color-text-primary)]">Save</span> to
                write it, then <span className="font-medium text-[var(--color-text-primary)]">Reload</span> to apply it to the running engine. Fields
                marked <span className="font-medium" style={{ color: RESTART_META.process.color }}>Needs full restart</span> only
                take effect the next time you launch Charlie, even after Reload.
              </p>
            </div>

            <div className="flex items-center gap-3 shrink-0">
              <span className="text-sm text-[var(--color-text-muted)]">
                {hasPending ? `${dirtyKeys.size} unsaved` : "No unsaved changes"}
              </span>
              <button
                onClick={() => void handleSaveAll()}
                disabled={!hasPending || saving}
                className="px-4 py-2 rounded-xl text-sm font-semibold bg-[var(--color-status-success)] text-white transition hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
              >
                {saving ? "Saving…" : "Save"}
              </button>
              <button
                onClick={() => void handleReload()}
                disabled={reloadingEngine}
                className="px-4 py-2 rounded-xl text-sm font-semibold bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-glass-border-hover)] transition disabled:opacity-60 cursor-pointer flex items-center gap-2"
              >
                <svg viewBox="0 0 24 24" className={`w-4 h-4 ${reloadingEngine ? "animate-spin" : ""}`} fill="none" stroke="currentColor" strokeWidth="2.5">
                  <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
                </svg>
                {reloadingEngine ? "Reloading…" : reloadStatus === "done" ? "Reloaded" : reloadStatus === "error" ? "Reload failed" : "Reload"}
              </button>
            </div>
          </div>

          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search settings..."
            className="w-full max-w-md mt-5 bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] rounded-xl px-4 py-2.5 text-sm text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-glass-border-hover)] transition"
          />
        </div>

        {loading ? (
          <div className="flex flex-col items-center justify-center py-24 gap-3">
            <div className="w-8 h-8 rounded-full border-2 border-t-transparent border-[var(--color-text-muted)] animate-spin" />
            <p className="text-sm text-[var(--color-text-muted)] font-mono">Loading settings...</p>
          </div>
        ) : visibleGroups.length === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)] font-mono text-center py-24">
            No settings match &quot;{query}&quot;.
          </p>
        ) : (
          <div className="mt-2 pb-16 columns-1 lg:columns-2 2xl:columns-3 gap-5">
            {visibleGroups.map((group) => (
              <section key={group} className="glass rounded-2xl p-5 flex flex-col gap-4 mb-5 break-inside-avoid">
                <div>
                  <h2 className="font-display text-lg font-semibold text-[var(--color-text-primary)]">{group}</h2>
                  <p className="text-sm text-[var(--color-text-muted)] mt-1">{GROUP_HELP[group]}</p>
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-3 items-stretch content-start">
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
            ))}
          </div>
        )}
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
      className={`${wide ? "col-span-2" : "col-span-2 sm:col-span-1"} h-full flex flex-col gap-2 rounded-lg border border-[var(--color-glass-border)] bg-[var(--color-surface-hover)] p-3`}
    >
      <div>
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-sm font-semibold text-[var(--color-text-primary)]">{spec.label}</span>
          {saveState === "saved" && <span className="text-xs text-[var(--color-status-success)]">saved</span>}
          {saveState === "error" && <span className="text-xs text-[var(--color-status-error)]">failed</span>}
        </div>
        <div className="flex items-center gap-1.5 flex-wrap mt-0.5">
          <span className="text-[11px] font-mono text-[var(--color-text-muted)]">{spec.key}</span>
          {restartMeta && (
            <span
              className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded-full"
              style={{ color: restartMeta.color, background: restartMeta.bg }}
            >
              {restartMeta.label}
            </span>
          )}
        </div>
      </div>

      <div className="mt-auto">
        {spec.type === "bool" ? (
          <label className="inline-flex items-center gap-2.5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={Boolean(value)}
              onChange={(e) => onChange(e.target.checked)}
              className="w-4 h-4 rounded"
            />
            <span className="text-sm text-[var(--color-text-secondary)]">{Boolean(value) ? "Enabled" : "Disabled"}</span>
          </label>
        ) : options ? (
          <select
            value={String(value ?? "")}
            onChange={(e) => onChange(e.target.value)}
            className="w-full bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-glass-border-hover)] transition"
          >
            {options.map((o) => (
              <option key={o.value} value={o.value}>
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
            className="w-full bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] rounded-lg px-3 py-2 text-sm font-mono text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-glass-border-hover)] transition resize-y"
          />
        ) : (
          <input
            type={spec.secret ? "password" : spec.type === "int" || spec.type === "float" ? "number" : "text"}
            value={String(value ?? "")}
            onChange={(e) => onChange(e.target.value)}
            placeholder={spec.secret ? (spec.is_set ? "Set -- leave blank to keep" : "Not set") : ""}
            step={spec.type === "float" ? "any" : undefined}
            className="w-full bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] rounded-lg px-3 py-2 text-sm font-mono text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-glass-border-hover)] transition"
          />
        )}
      </div>
    </div>
  );
}
