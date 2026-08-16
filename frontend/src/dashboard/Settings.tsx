import { useEffect, useMemo, useState, type ReactElement } from "react";
import { Panel } from "./Panel";
import { useMapStore } from "../map/mapStore";

interface ConfigField {
  key: string;
  label: string;
  group: string;
  type: "str" | "int" | "float" | "bool";
  secret: boolean;
  restart: string | null;
  value: unknown;
  is_set: boolean;
}

interface AuditEntry {
  id: string;
  created_at: string;
  tool_name: string;
  arguments: string;
  outcome: string;
}

interface CapabilitySnapshot {
  tools?: Array<{ name: string }>;
  runtime?: Record<string, { status?: string; detail?: string }>;
}

interface ModelSnapshot {
  active_model?: string;
  models?: string[];
  has_api_key?: boolean;
}

function fieldValue(field: ConfigField, drafts: Record<string, unknown>): unknown {
  return field.key in drafts ? drafts[field.key] : field.value;
}

const CATEGORIES = [
  "All",
  "General",
  "Voice",
  "Appearance",
  "HUD",
  "Map",
  "Pet",
  "Models",
  "Memory",
  "Automation",
  "Privacy",
  "Tools / MCP",
  "Integrations",
  "System",
  "Developer",
  "Audit & Diagnostics",
];

export function Settings({ embed = false }: { embed?: boolean } = {}): ReactElement {
  const [fields, setFields] = useState<ConfigField[]>([]);
  const [drafts, setDrafts] = useState<Record<string, unknown>>({});
  const [status, setStatus] = useState("Loading settings...");
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [capabilities, setCapabilities] = useState<CapabilitySnapshot>({});
  const [modelSnapshot, setModelSnapshot] = useState<ModelSnapshot>({});
  const [modelsLoading, setModelsLoading] = useState(true);
  const [activeCategory, setActiveCategory] = useState("All");

  useEffect(() => {
    void fetch("/api/config")
      .then(async (response) =>
        response.ok
          ? (response.json() as Promise<{ fields?: ConfigField[] }>)
          : Promise.reject(new Error("Settings unavailable"))
      )
      .then((data) => {
        setFields(Array.isArray(data.fields) ? data.fields : []);
        setStatus("");
      })
      .catch(() => setStatus("Settings unavailable."));
  }, []);

  async function refreshModels(): Promise<void> {
    setModelsLoading(true);
    try {
      const response = await fetch("/api/models");
      if (!response.ok) throw new Error("Models unavailable");
      setModelSnapshot((await response.json()) as ModelSnapshot);
    } catch {
      setModelSnapshot({});
    } finally {
      setModelsLoading(false);
    }
  }

  useEffect(() => {
    void fetch("/api/capabilities")
      .then(async (response) =>
        response.ok
          ? (response.json() as Promise<CapabilitySnapshot>)
          : Promise.reject(new Error("Capabilities unavailable"))
      )
      .then(setCapabilities)
      .catch(() => setCapabilities({}));
  }, []);

  useEffect(() => {
    void refreshModels();
  }, []);

  useEffect(() => {
    void fetch("/api/audit")
      .then(async (response) =>
        response.ok
          ? (response.json() as Promise<{ entries?: AuditEntry[] }>)
          : Promise.reject(new Error("Audit unavailable"))
      )
      .then((data) => setAudit(Array.isArray(data.entries) ? data.entries : []))
      .catch(() => setAudit([]));
  }, []);

  const groups = useMemo(
    () =>
      fields.reduce<Record<string, ConfigField[]>>((result, field) => {
        (result[field.group] ??= []).push(field);
        return result;
      }, {}),
    [fields]
  );

  const availableModels = useMemo(
    () =>
      Array.from(
        new Set([
          String(fields.find((field) => field.key === "LLM_MODEL")?.value ?? ""),
          ...(modelSnapshot.models ?? []),
        ])
      ).filter(Boolean),
    [fields, modelSnapshot.models]
  );

  async function save(): Promise<void> {
    if (Object.keys(drafts).length === 0) return;
    setStatus("Saving...");
    try {
      const response = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(drafts),
      });
      if (!response.ok) throw new Error("Save failed");
      setDrafts({});
      setStatus("Saved. Reload required settings when ready.");
    } catch {
      setStatus("Settings save failed.");
    }
  }

  async function reload(): Promise<void> {
    setStatus("Reloading...");
    try {
      const response = await fetch("/api/config/reload", { method: "POST" });
      setStatus(response.ok ? "Reload requested." : "Reload unavailable.");
    } catch {
      setStatus("Reload unavailable.");
    }
  }

  async function exportAudit(): Promise<void> {
    const response = await fetch("/api/audit/export");
    if (!response.ok) return;
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "charlie-audit.json";
    link.click();
    URL.revokeObjectURL(link.href);
  }

  // Filter visible groups according to active category
  const visibleGroups = useMemo(() => {
    if (activeCategory === "All") return groups;
    if (activeCategory === "Audit & Diagnostics") return {};
    return Object.fromEntries(
      Object.entries(groups).filter(
        ([group]) => group.toLowerCase() === activeCategory.toLowerCase()
      )
    );
  }, [groups, activeCategory]);

  const body = (
    <div className="settings-workspace font-mono text-left flex flex-col h-full">
      {/* 1. Header Toolbar */}
      <div className="settings-intro mb-3 flex items-center justify-between border-b border-cyan-500/20 pb-2.5">
        <div>
          <h3 className="text-xs font-bold text-cyan-200 uppercase tracking-wide">
            Runtime Controls
          </h3>
          <p className="text-[11px] text-slate-400 font-sans mt-0.5">
            Settings are applied locally and activated on runtime reload.
          </p>
        </div>
        <button
          type="button"
          className="px-3 py-1 text-xs rounded bg-cyan-950/80 border border-cyan-500/40 text-cyan-300 hover:bg-cyan-900/60 transition cursor-pointer"
          onClick={() => void refreshModels()}
          disabled={modelsLoading}
        >
          {modelsLoading ? "Discovering..." : "Refresh Models"}
        </button>
      </div>

      {/* 2. Main Two-Column Layout: Sidebar Categories + Content Form */}
      <div className="flex-1 flex gap-5 min-h-[360px] overflow-hidden">
        {/* Category Sidebar */}
        <div className="w-44 border-r border-cyan-500/15 pr-3 flex flex-col gap-1 overflow-y-auto select-none">
          <div className="text-[9px] text-cyan-400/60 font-bold uppercase tracking-widest px-2 mb-1">
            Categories
          </div>
          {CATEGORIES.map((cat) => {
            const hasData =
              cat === "All" ||
              cat === "Audit & Diagnostics" ||
              Object.keys(groups).some((g) => g.toLowerCase() === cat.toLowerCase());

            return (
              <button
                key={cat}
                type="button"
                onClick={() => setActiveCategory(cat)}
                className={`px-2.5 py-1.5 text-xs text-left rounded-lg transition cursor-pointer flex items-center justify-between ${
                  activeCategory === cat
                    ? "bg-cyan-950/90 border border-cyan-400/50 text-cyan-200 shadow-sm shadow-cyan-500/20"
                    : "text-slate-400 hover:text-cyan-200 hover:bg-cyan-950/30"
                } ${!hasData && cat !== "All" && cat !== "Audit & Diagnostics" ? "opacity-40" : ""}`}
              >
                <span>{cat}</span>
                {activeCategory === cat && <span className="w-1.5 h-1.5 rounded-full bg-cyan-400" />}
              </button>
            );
          })}
        </div>

        {/* Content Panel */}
        <div className="flex-1 overflow-y-auto pr-1 space-y-4">
          {/* Active Category Fields */}
          {Object.keys(visibleGroups).length > 0 ? (
            Object.entries(visibleGroups).map(([group, groupFields]) => (
              <section
                className="settings-group p-3.5 rounded-xl border border-cyan-500/15 bg-slate-950/60"
                key={group}
              >
                <h3 className="text-xs font-bold text-cyan-400 uppercase tracking-wider mb-2.5 border-b border-cyan-500/10 pb-1 flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-400" />
                  {group}
                </h3>
                <div className="space-y-2.5">
                  {groupFields.map((field) => (
                    <label
                      className="settings-field flex items-center justify-between gap-4 text-xs text-slate-300 font-sans"
                      key={field.key}
                    >
                      <span className="max-w-[220px]">
                        <strong className="text-slate-200 font-mono text-[11px] block">
                          {field.label}
                        </strong>
                        {field.restart ? (
                          <small className="block text-[10px] text-amber-400/80 mt-0.5">
                            Restart required: {field.restart}
                          </small>
                        ) : null}
                      </span>

                      {field.secret ? (
                        <input
                          aria-label={field.label}
                          type="password"
                          className="px-2.5 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono w-52 text-right"
                          placeholder={field.is_set ? "Configured" : "Not configured"}
                          onChange={(event) =>
                            setDrafts((current) => ({
                              ...current,
                              [field.key]: event.target.value,
                            }))
                          }
                        />
                      ) : field.type === "bool" ? (
                        <input
                          aria-label={field.label}
                          type="checkbox"
                          className="w-4 h-4 accent-cyan-400 cursor-pointer"
                          checked={Boolean(fieldValue(field, drafts))}
                          onChange={(event) =>
                            setDrafts((current) => ({
                              ...current,
                              [field.key]: event.target.checked,
                            }))
                          }
                        />
                      ) : field.key === "LLM_MODEL" && (modelSnapshot.models?.length ?? 0) > 0 ? (
                        <>
                          <input
                            aria-label={field.label}
                            list="charlie-model-options"
                            className="px-2.5 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono w-52 text-right"
                            value={String(fieldValue(field, drafts) ?? "")}
                            onChange={(event) =>
                              setDrafts((current) => ({
                                ...current,
                                [field.key]: event.target.value,
                              }))
                            }
                          />
                          <datalist id="charlie-model-options">
                            {availableModels.map((model) => (
                              <option key={model} value={model} />
                            ))}
                          </datalist>
                        </>
                      ) : (
                        <input
                          aria-label={field.label}
                          type={
                            field.type === "int" || field.type === "float"
                              ? "number"
                              : "text"
                          }
                          className="px-2.5 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono w-52 text-right"
                          value={String(fieldValue(field, drafts) ?? "")}
                          onChange={(event) =>
                            setDrafts((current) => ({
                              ...current,
                              [field.key]: event.target.value,
                            }))
                          }
                        />
                      )}
                    </label>
                  ))}
                </div>
              </section>
            ))
          ) : activeCategory !== "Audit & Diagnostics" && activeCategory !== "Map" ? (
            <div className="p-6 rounded-xl border border-cyan-500/10 bg-slate-950/40 text-center text-slate-400 text-xs">
              No configuration properties in category &ldquo;{activeCategory}&rdquo;.
            </div>
          ) : null}

          {/* Map & Spatial Intelligence Section */}
          {(activeCategory === "All" || activeCategory === "Map") && (
            <section className="settings-group settings-map p-3.5 rounded-xl border border-cyan-500/15 bg-slate-950/60 space-y-3">
              <h3 className="text-xs font-bold text-cyan-400 uppercase tracking-wider mb-2.5 border-b border-cyan-500/10 pb-1 flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-cyan-400" />
                Spatial Intelligence Map
              </h3>
              <div className="space-y-3 font-sans text-xs">
                <label className="flex items-center justify-between gap-4 text-slate-300">
                  <span className="max-w-[240px]">
                    <strong className="text-slate-200 font-mono text-[11px] block">
                      Provider Mode
                    </strong>
                    <small className="text-[10px] text-slate-400">
                      Hybrid falls back to online tiles when offline PMTiles are unavailable
                    </small>
                  </span>
                  <select
                    className="px-2.5 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono w-44"
                    value={useMapStore.getState().providerMode}
                    onChange={(e) =>
                      useMapStore.getState().setProviderMode(e.target.value as "hybrid" | "online" | "offline")
                    }
                  >
                    <option value="hybrid">Hybrid (Auto)</option>
                    <option value="online">Online Only</option>
                    <option value="offline">Offline / PMTiles</option>
                  </select>
                </label>

                <label className="flex items-center justify-between gap-4 text-slate-300">
                  <span className="max-w-[240px]">
                    <strong className="text-slate-200 font-mono text-[11px] block">
                      Rendering Quality
                    </strong>
                    <small className="text-[10px] text-slate-400">
                      Adaptive visual effects and particle density
                    </small>
                  </span>
                  <select
                    className="px-2.5 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono w-44"
                    value={useMapStore.getState().quality}
                    onChange={(e) =>
                      useMapStore.getState().setQuality(e.target.value as "auto" | "high" | "medium" | "low")
                    }
                  >
                    <option value="auto">Auto (Hardware)</option>
                    <option value="high">High Fidelity</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low / Fast</option>
                  </select>
                </label>

                <label className="flex items-center justify-between gap-4 text-slate-300">
                  <span className="max-w-[240px]">
                    <strong className="text-slate-200 font-mono text-[11px] block">
                      Local PMTiles Source
                    </strong>
                    <small className="text-[10px] text-slate-400">
                      Optional local PMTiles file path or URL for air-gapped mapping
                    </small>
                  </span>
                  <input
                    type="text"
                    placeholder="/tiles/planet.pmtiles"
                    className="px-2.5 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono w-52 text-right placeholder-slate-600"
                    defaultValue={useMapStore.getState().pmtilesUrl || ""}
                    onBlur={(e) => useMapStore.getState().setPmtilesUrl(e.target.value || null)}
                  />
                </label>
              </div>
            </section>
          )}

          {/* Audit & Diagnostics Section */}
          {(activeCategory === "All" || activeCategory === "Audit & Diagnostics") && (
            <>
              <section className="settings-group settings-audit p-3.5 rounded-xl border border-cyan-500/15 bg-slate-950/60">
                <h3 className="text-xs font-bold text-cyan-400 uppercase tracking-wider mb-2 border-b border-cyan-500/10 pb-1">
                  Audit Trail
                </h3>
                {audit.length === 0 ? (
                  <p className="text-[11px] text-slate-500 italic">No audit entries recorded.</p>
                ) : (
                  <div className="space-y-1.5 text-[11px] font-mono">
                    {audit.map((entry) => (
                      <p key={entry.id} className="flex justify-between text-slate-300">
                        <time className="text-slate-500">
                          {isNaN(new Date(entry.created_at).getTime())
                            ? entry.created_at
                            : new Date(entry.created_at).toLocaleTimeString()}
                        </time>
                        <strong className="text-cyan-300 font-normal">{entry.tool_name}</strong>
                        <span className="text-emerald-400">{entry.outcome}</span>
                      </p>
                    ))}
                  </div>
                )}
              </section>

              <section className="settings-group settings-capabilities p-3.5 rounded-xl border border-cyan-500/15 bg-slate-950/60">
                <h3 className="text-xs font-bold text-cyan-400 uppercase tracking-wider mb-1">
                  Live Capabilities & Diagnostics
                </h3>
                <p className="text-[11px] text-slate-400 mb-2">
                  {capabilities.tools?.length ?? 0} registered tools
                </p>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  {Object.entries(capabilities.runtime ?? {}).map(([name, health]) => (
                    <p
                      key={name}
                      className="flex justify-between p-2 rounded bg-slate-900/60 border border-cyan-500/10"
                    >
                      <strong className="text-cyan-200">{name}</strong>
                      <span className="text-emerald-400">
                        {health.detail ?? health.status ?? "Unknown"}
                      </span>
                    </p>
                  ))}
                </div>
              </section>
            </>
          )}
        </div>
      </div>

      {status ? (
        <p className="text-xs text-amber-400 mt-2 italic font-mono">{status}</p>
      ) : null}

      {/* 3. Footer Action Buttons */}
      <footer className="settings-actions mt-3 pt-2.5 border-t border-cyan-500/20 flex gap-2 justify-end">
        <button
          type="button"
          className="px-3.5 py-1 text-xs rounded bg-cyan-500/20 border border-cyan-400/40 text-cyan-200 hover:bg-cyan-500/30 transition cursor-pointer disabled:opacity-40"
          onClick={() => void save()}
          disabled={Object.keys(drafts).length === 0}
        >
          Save Changes
        </button>
        <button
          type="button"
          className="px-3.5 py-1 text-xs rounded bg-slate-900 border border-cyan-500/30 text-slate-300 hover:text-cyan-200 transition cursor-pointer"
          onClick={() => void reload()}
        >
          Reload Runtime
        </button>
        <button
          type="button"
          className="px-3.5 py-1 text-xs rounded bg-slate-900 border border-cyan-500/30 text-slate-300 hover:text-cyan-200 transition cursor-pointer"
          onClick={() => void exportAudit()}
        >
          Export Audit
        </button>
      </footer>
    </div>
  );

  if (embed) {
    return body;
  }

  return (
    <Panel id="settings" title="Settings">
      {body}
    </Panel>
  );
}
