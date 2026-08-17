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

interface MCPServerInfo {
  name: string;
  command: string;
  args: string[];
  running: boolean;
  status: string;
  tools_count: number;
  tools: Array<{ name: string; description: string }>;
}

interface MemoryItem {
  id: string;
  category: string;
  content: string;
  subject?: string;
  predicate?: string;
  object?: string;
  created_at?: string;
}

interface PrivacyCategoryUsage {
  name: string;
  bytes: number;
  formatted: string;
  path?: string;
}

interface PrivacySummary {
  total_bytes: number;
  total_formatted: string;
  categories: Record<string, PrivacyCategoryUsage>;
}

interface DeveloperDiagnostics {
  tasks?: Array<{ id: string; origin: string; status: string; progress?: number; started_at?: string }>;
  leases?: Record<string, string>;
  telemetry?: {
    llm_error_rate?: number;
    tool_error_rate?: number;
    tool_stats?: Record<string, { calls: number; errors: number; error_rate: number }>;
  };
  system?: {
    uptime_seconds?: number;
    active_threads?: number;
    active_ws_connections?: number;
    subsystems?: Record<string, { status?: string }>;
  };
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

const CATEGORY_MAP: Record<string, string> = {
  "General": "General",
  "Voice & Speech": "Voice",
  "VAD & ASR Tuning": "Voice",
  "Voice": "Voice",
  "Appearance": "Appearance",
  "HUD": "HUD",
  "Map": "Map",
  "Companion": "Pet",
  "Pet": "Pet",
  "LLM": "Models",
  "Models": "Models",
  "Vision": "Models",
  "Memory Files": "Memory",
  "Memory": "Memory",
  "Chat Behavior": "Automation",
  "Autonomy": "Automation",
  "Automation": "Automation",
  "Privacy": "Privacy",
  "Logging & Redaction": "Privacy",
  "Search Providers": "Tools / MCP",
  "Web Research": "Tools / MCP",
  "Research Advanced": "Tools / MCP",
  "MCP": "Tools / MCP",
  "Tools / MCP": "Tools / MCP",
  "Plugins": "Tools / MCP",
  "Telegram": "Integrations",
  "Calendar": "Integrations",
  "Media": "Integrations",
  "Integrations": "Integrations",
  "Server": "System",
  "System": "System",
  "Developer": "Developer",
  "Debug": "Developer",
};

function getCategoryForGroup(group: string): string {
  return CATEGORY_MAP[group] || group;
}

export function Settings({ embed = false }: { embed?: boolean } = {}): ReactElement {
  const [fields, setFields] = useState<ConfigField[]>([
    { key: "ASSISTANT_NAME", label: "Assistant Identity Name", value: "C.H.A.R.L.I.E.", group: "General", type: "str", secret: false, restart: null, is_set: true },
    { key: "VOICE_SYNTHESIS", label: "Voice Synthesis Engine", value: "Kokoro ONNX (Local)", group: "Voice", type: "str", secret: false, restart: null, is_set: true },
    { key: "THEME_ACCENT", label: "Interface Accent Theme", value: "Cyan Tactical", group: "Appearance", type: "str", secret: false, restart: null, is_set: true },
    { key: "HUD_AUTO_DISMISS", label: "Auto-Dismiss Transient Widgets", value: true, group: "HUD", type: "bool", secret: false, restart: null, is_set: true },
  ]);
  const [drafts, setDrafts] = useState<Record<string, unknown>>({});
  const [status, setStatus] = useState("");
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [capabilities, setCapabilities] = useState<CapabilitySnapshot>({});
  const [modelSnapshot, setModelSnapshot] = useState<ModelSnapshot>({});
  const [modelsLoading, setModelsLoading] = useState(false);
  const [activeCategory, setActiveCategory] = useState("All");

  // MCP State
  const [mcpServers, setMcpServers] = useState<MCPServerInfo[]>([]);
  const [mcpLoading, setMcpLoading] = useState(false);
  const [newMcpName, setNewMcpName] = useState("");
  const [newMcpCommand, setNewMcpCommand] = useState("");
  const [newMcpArgs, setNewMcpArgs] = useState("");

  // Memory State
  const [memoryItems, setMemoryItems] = useState<MemoryItem[]>([]);
  const [memoryQuery, setMemoryQuery] = useState("");
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [newMemContent, setNewMemContent] = useState("");
  const [newMemCategory, setNewMemCategory] = useState("fact");
  const [editingMemId, setEditingMemId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");

  // Privacy State
  const [privacySummary, setPrivacySummary] = useState<PrivacySummary | null>(null);
  const [privacyLoading, setPrivacyLoading] = useState(false);

  // Developer State
  const [devDiagnostics, setDevDiagnostics] = useState<DeveloperDiagnostics | null>(null);
  const [devLogs, setDevLogs] = useState<string[]>([]);
  const [devLoading, setDevLoading] = useState(false);

  // Reactive Map Settings Hooks
  const mapProviderMode = useMapStore((s) => s.providerMode);
  const mapQuality = useMapStore((s) => s.quality);
  const mapPmtilesUrl = useMapStore((s) => s.pmtilesUrl);
  const mapAvailableArchives = useMapStore((s) => s.availableArchives);
  const setMapProviderMode = useMapStore((s) => s.setProviderMode);
  const setMapQuality = useMapStore((s) => s.setQuality);
  const setMapPmtilesUrl = useMapStore((s) => s.setPmtilesUrl);
  const fetchAvailableArchives = useMapStore((s) => s.fetchAvailableArchives);

  useEffect(() => {
    fetchAvailableArchives();
  }, [fetchAvailableArchives]);

  useEffect(() => {
    void fetch("/api/config")
      .then(async (response) =>
        response.ok
          ? (response.json() as Promise<{ fields?: ConfigField[] }>)
          : null
      )
      .then((data) => {
        if (data && Array.isArray(data.fields) && data.fields.length > 0) {
          setFields(data.fields);
        }
      })
      .catch(() => {
        // Fallback default fields retained
      });
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

  // Fetch MCP Servers
  async function fetchMcpServers(): Promise<void> {
    setMcpLoading(true);
    try {
      const res = await fetch("/api/mcp/servers");
      if (res.ok) {
        const data = (await res.json()) as { servers?: MCPServerInfo[] };
        setMcpServers(Array.isArray(data.servers) ? data.servers : []);
      }
    } catch {
      setMcpServers([]);
    } finally {
      setMcpLoading(false);
    }
  }

  // Fetch Memory Items
  async function fetchMemoryItems(q = ""): Promise<void> {
    setMemoryLoading(true);
    try {
      const url = q.trim() ? `/api/memory/search?q=${encodeURIComponent(q.trim())}` : "/api/memory/items";
      const res = await fetch(url);
      if (res.ok) {
        const data = (await res.json()) as { items?: MemoryItem[] };
        setMemoryItems(Array.isArray(data.items) ? data.items : []);
      }
    } catch {
      setMemoryItems([]);
    } finally {
      setMemoryLoading(false);
    }
  }

  // Fetch Privacy Summary
  async function fetchPrivacySummary(): Promise<void> {
    setPrivacyLoading(true);
    try {
      const res = await fetch("/api/privacy/summary");
      if (res.ok) {
        setPrivacySummary((await res.json()) as PrivacySummary);
      }
    } catch {
      setPrivacySummary(null);
    } finally {
      setPrivacyLoading(false);
    }
  }

  // Fetch Developer Diagnostics
  async function fetchDeveloperDiagnostics(): Promise<void> {
    setDevLoading(true);
    try {
      const [diagRes, logsRes] = await Promise.all([
        fetch("/api/developer/diagnostics"),
        fetch("/api/developer/logs?limit=50"),
      ]);
      if (diagRes.ok) {
        const data = (await diagRes.json()) as { diagnostics?: DeveloperDiagnostics };
        setDevDiagnostics(data.diagnostics ?? null);
      }
      if (logsRes.ok) {
        const data = (await logsRes.json()) as { lines?: string[] };
        setDevLogs(Array.isArray(data.lines) ? data.lines : []);
      }
    } catch {
      setDevDiagnostics(null);
      setDevLogs([]);
    } finally {
      setDevLoading(false);
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
    if (activeCategory === "Tools / MCP" || activeCategory === "All") {
      void fetchMcpServers();
    }
    if (activeCategory === "Memory" || activeCategory === "All") {
      void fetchMemoryItems();
    }
    if (activeCategory === "Privacy" || activeCategory === "All") {
      void fetchPrivacySummary();
    }
    if (activeCategory === "Developer" || activeCategory === "All") {
      void fetchDeveloperDiagnostics();
    }
  }, [activeCategory]);

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

  // MCP Actions
  async function handleAddMcpServer(): Promise<void> {
    if (!newMcpName.trim() || !newMcpCommand.trim()) return;
    try {
      const argsList = newMcpArgs.split(",").map((a) => a.trim()).filter(Boolean);
      const res = await fetch("/api/mcp/servers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newMcpName.trim(), command: newMcpCommand.trim(), args: argsList }),
      });
      if (res.ok) {
        setNewMcpName("");
        setNewMcpCommand("");
        setNewMcpArgs("");
        await fetchMcpServers();
      }
    } catch (e) {
      console.error(e);
    }
  }

  async function handleMcpAction(name: string, action: "connect" | "disconnect" | "restart" | "delete"): Promise<void> {
    try {
      const url = action === "delete" ? `/api/mcp/servers/${encodeURIComponent(name)}` : `/api/mcp/servers/${encodeURIComponent(name)}/${action}`;
      const method = action === "delete" ? "DELETE" : "POST";
      const res = await fetch(url, { method });
      if (res.ok) {
        await fetchMcpServers();
      }
    } catch (e) {
      console.error(e);
    }
  }

  // Memory Actions
  async function handleAddMemory(): Promise<void> {
    if (!newMemContent.trim()) return;
    try {
      const res = await fetch("/api/memory/items", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: newMemCategory, content: newMemContent.trim() }),
      });
      if (res.ok) {
        setNewMemContent("");
        await fetchMemoryItems(memoryQuery);
      }
    } catch (e) {
      console.error(e);
    }
  }

  async function handleSaveEditMemory(id: string): Promise<void> {
    if (!editContent.trim()) return;
    try {
      const res = await fetch(`/api/memory/items/${encodeURIComponent(id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: editContent.trim() }),
      });
      if (res.ok) {
        setEditingMemId(null);
        setEditContent("");
        await fetchMemoryItems(memoryQuery);
      }
    } catch (e) {
      console.error(e);
    }
  }

  async function handleDeleteMemory(id: string): Promise<void> {
    try {
      const res = await fetch(`/api/memory/items/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (res.ok) {
        await fetchMemoryItems(memoryQuery);
      }
    } catch (e) {
      console.error(e);
    }
  }

  async function handleClearMemory(category?: string): Promise<void> {
    try {
      const res = await fetch("/api/memory/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: category || "all" }),
      });
      if (res.ok) {
        await fetchMemoryItems(memoryQuery);
      }
    } catch (e) {
      console.error(e);
    }
  }

  async function handleExportMemory(): Promise<void> {
    try {
      const res = await fetch("/api/memory/export");
      if (!res.ok) return;
      const data = await res.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "charlie-memory.json";
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (e) {
      console.error(e);
    }
  }

  // Privacy Actions
  async function handlePurge(category: string): Promise<void> {
    try {
      const res = await fetch("/api/privacy/purge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category }),
      });
      if (res.ok) {
        await fetchPrivacySummary();
      }
    } catch (e) {
      console.error(e);
    }
  }

  // Filter visible groups according to active category
  const visibleGroups = useMemo(() => {
    if (activeCategory === "All") return groups;
    if (activeCategory === "Audit & Diagnostics") return {};
    if (activeCategory === "Map") {
      return Object.fromEntries(
        Object.entries(groups).filter(
          ([group]) => getCategoryForGroup(group).toLowerCase() === "map"
        )
      );
    }
    return Object.fromEntries(
      Object.entries(groups).filter(
        ([group]) =>
          getCategoryForGroup(group).toLowerCase() === activeCategory.toLowerCase() ||
          group.toLowerCase() === activeCategory.toLowerCase()
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
              cat === "Map" ||
              cat === "Tools / MCP" ||
              cat === "Memory" ||
              cat === "Privacy" ||
              cat === "Developer" ||
              Object.keys(groups).some(
                (g) =>
                  getCategoryForGroup(g).toLowerCase() === cat.toLowerCase() ||
                  g.toLowerCase() === cat.toLowerCase()
              );

            return (
              <button
                key={cat}
                type="button"
                onClick={() => setActiveCategory(cat)}
                className={`px-2.5 py-1.5 text-xs text-left rounded-lg transition cursor-pointer flex items-center justify-between ${
                  activeCategory === cat
                    ? "bg-cyan-950/90 border border-cyan-400/50 text-cyan-200 shadow-sm shadow-cyan-500/20"
                    : "text-slate-400 hover:text-cyan-200 hover:bg-cyan-950/30"
                } ${!hasData && cat !== "All" ? "opacity-40" : ""}`}
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
                          placeholder={field.is_set ? "•••••••• (configured)" : "Not configured"}
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
                            field.secret
                              ? "password"
                              : field.type === "int" || field.type === "float"
                              ? "number"
                              : "text"
                          }
                          placeholder={field.secret && field.is_set ? "•••••••• (configured)" : undefined}
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
          ) : activeCategory !== "Audit & Diagnostics" &&
            activeCategory !== "Map" &&
            activeCategory !== "Tools / MCP" &&
            activeCategory !== "Memory" &&
            activeCategory !== "Privacy" &&
            activeCategory !== "Developer" ? (
            <div className="p-6 rounded-xl border border-cyan-500/10 bg-slate-950/40 text-center text-slate-400 text-xs">
              No configuration properties in category &ldquo;{activeCategory}&rdquo;.
            </div>
          ) : null}

          {/* MCP Server Management Section */}
          {(activeCategory === "All" || activeCategory === "Tools / MCP") && (
            <section className="settings-group p-3.5 rounded-xl border border-cyan-500/15 bg-slate-950/60 space-y-3">
              <div className="flex items-center justify-between border-b border-cyan-500/10 pb-1">
                <h3 className="text-xs font-bold text-cyan-400 uppercase tracking-wider flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-400" />
                  Model Context Protocol (MCP) Servers
                </h3>
                <button
                  type="button"
                  onClick={() => void fetchMcpServers()}
                  disabled={mcpLoading}
                  className="text-[10px] text-cyan-300 hover:underline cursor-pointer"
                >
                  {mcpLoading ? "Refreshing..." : "Refresh"}
                </button>
              </div>

              {/* Server List */}
              {mcpServers.length === 0 ? (
                <p className="text-[11px] text-slate-500 italic py-2">
                  No MCP servers currently registered. Add a server below.
                </p>
              ) : (
                <div className="space-y-2">
                  {mcpServers.map((srv) => (
                    <div
                      key={srv.name}
                      className="p-2.5 rounded-lg bg-slate-900/80 border border-cyan-500/20 flex flex-col gap-2"
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span
                            className={`w-2 h-2 rounded-full ${
                              srv.running || srv.status === "connected"
                                ? "bg-emerald-400 shadow-sm shadow-emerald-400"
                                : "bg-slate-500"
                            }`}
                          />
                          <span className="text-xs font-bold text-cyan-200">{srv.name}</span>
                          <span className="text-[10px] text-slate-400 font-mono">
                            {srv.command} {srv.args.join(" ")}
                          </span>
                        </div>
                        <div className="flex items-center gap-1.5">
                          {srv.running ? (
                            <button
                              type="button"
                              onClick={() => void handleMcpAction(srv.name, "disconnect")}
                              className="px-2 py-0.5 text-[10px] rounded bg-amber-950/60 border border-amber-500/30 text-amber-300 hover:bg-amber-900/60 transition cursor-pointer"
                            >
                              Disconnect
                            </button>
                          ) : (
                            <button
                              type="button"
                              onClick={() => void handleMcpAction(srv.name, "connect")}
                              className="px-2 py-0.5 text-[10px] rounded bg-emerald-950/60 border border-emerald-500/30 text-emerald-300 hover:bg-emerald-900/60 transition cursor-pointer"
                            >
                              Connect
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => void handleMcpAction(srv.name, "restart")}
                            className="px-2 py-0.5 text-[10px] rounded bg-cyan-950/60 border border-cyan-500/30 text-cyan-300 hover:bg-cyan-900/60 transition cursor-pointer"
                          >
                            Restart
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleMcpAction(srv.name, "delete")}
                            className="px-2 py-0.5 text-[10px] rounded bg-rose-950/60 border border-rose-500/30 text-rose-300 hover:bg-rose-900/60 transition cursor-pointer"
                          >
                            Remove
                          </button>
                        </div>
                      </div>

                      {srv.tools && srv.tools.length > 0 ? (
                        <div className="pt-1.5 border-t border-cyan-500/10 flex flex-wrap gap-1">
                          <span className="text-[10px] text-slate-400 mr-1">Tools ({srv.tools.length}):</span>
                          {srv.tools.map((t) => (
                            <span
                              key={t.name}
                              className="px-1.5 py-0.5 rounded text-[9px] bg-cyan-950/80 border border-cyan-500/20 text-cyan-300 font-mono"
                              title={t.description}
                            >
                              {t.name}
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}

              {/* Add MCP Server Form */}
              <div className="pt-2 border-t border-cyan-500/15">
                <div className="text-[11px] font-bold text-slate-300 mb-2">Register MCP Server</div>
                <div className="grid grid-cols-3 gap-2 mb-2">
                  <input
                    type="text"
                    placeholder="Server Name (e.g. github)"
                    value={newMcpName}
                    onChange={(e) => setNewMcpName(e.target.value)}
                    className="px-2 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono"
                  />
                  <input
                    type="text"
                    placeholder="Command (e.g. npx or uvx)"
                    value={newMcpCommand}
                    onChange={(e) => setNewMcpCommand(e.target.value)}
                    className="px-2 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono"
                  />
                  <input
                    type="text"
                    placeholder="Args (comma separated)"
                    value={newMcpArgs}
                    onChange={(e) => setNewMcpArgs(e.target.value)}
                    className="px-2 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono"
                  />
                </div>
                <button
                  type="button"
                  onClick={() => void handleAddMcpServer()}
                  disabled={!newMcpName.trim() || !newMcpCommand.trim()}
                  className="px-3 py-1 text-xs rounded bg-cyan-950 border border-cyan-400/40 text-cyan-300 hover:bg-cyan-900/60 transition cursor-pointer disabled:opacity-40"
                >
                  + Add MCP Server
                </button>
              </div>
            </section>
          )}

          {/* Interactive Memory Management Section */}
          {(activeCategory === "All" || activeCategory === "Memory") && (
            <section className="settings-group p-3.5 rounded-xl border border-cyan-500/15 bg-slate-950/60 space-y-3">
              <div className="flex items-center justify-between border-b border-cyan-500/10 pb-1">
                <h3 className="text-xs font-bold text-cyan-400 uppercase tracking-wider flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-400" />
                  Knowledge & Memory Store
                </h3>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void handleExportMemory()}
                    className="text-[10px] text-cyan-300 hover:underline cursor-pointer"
                  >
                    Export JSON
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleClearMemory("all")}
                    className="text-[10px] text-rose-400 hover:underline cursor-pointer"
                  >
                    Clear All
                  </button>
                </div>
              </div>

              {/* Memory Search & Filter Bar */}
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="Search memories, facts, preferences..."
                  value={memoryQuery}
                  onChange={(e) => {
                    setMemoryQuery(e.target.value);
                    void fetchMemoryItems(e.target.value);
                  }}
                  className="flex-1 px-2.5 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono"
                />
                <button
                  type="button"
                  onClick={() => void fetchMemoryItems(memoryQuery)}
                  disabled={memoryLoading}
                  className="px-3 py-1 text-xs rounded bg-cyan-950 border border-cyan-400/40 text-cyan-300 hover:bg-cyan-900/60 transition cursor-pointer"
                >
                  {memoryLoading ? "Searching..." : "Search"}
                </button>
              </div>

              {/* Memory Items List */}
              <div className="space-y-1.5 max-h-60 overflow-y-auto pr-1">
                {memoryItems.length === 0 ? (
                  <p className="text-[11px] text-slate-500 italic py-2">
                    No memories found. Add entries below.
                  </p>
                ) : (
                  memoryItems.map((item) => (
                    <div
                      key={item.id}
                      className="p-2 rounded bg-slate-900/60 border border-cyan-500/10 flex items-center justify-between gap-3 text-xs"
                    >
                      {editingMemId === item.id ? (
                        <div className="flex-1 flex gap-2">
                          <input
                            type="text"
                            value={editContent}
                            onChange={(e) => setEditContent(e.target.value)}
                            className="flex-1 px-2 py-0.5 rounded bg-slate-950 border border-cyan-400 text-cyan-200 text-xs font-mono"
                          />
                          <button
                            type="button"
                            onClick={() => void handleSaveEditMemory(item.id)}
                            className="px-2 py-0.5 text-[10px] rounded bg-emerald-950 border border-emerald-400 text-emerald-300 cursor-pointer"
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            onClick={() => setEditingMemId(null)}
                            className="px-2 py-0.5 text-[10px] rounded bg-slate-800 text-slate-400 cursor-pointer"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <>
                          <div className="flex-1 flex items-baseline gap-2 overflow-hidden">
                            <span className="px-1.5 py-0.2 rounded text-[9px] uppercase font-bold bg-cyan-950 text-cyan-400 border border-cyan-500/30">
                              {item.category}
                            </span>
                            <span className="text-slate-200 truncate">{item.content}</span>
                          </div>
                          <div className="flex items-center gap-1.5">
                            <button
                              type="button"
                              onClick={() => {
                                setEditingMemId(item.id);
                                setEditContent(item.content);
                              }}
                              className="text-[10px] text-cyan-400 hover:text-cyan-200 cursor-pointer"
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              onClick={() => void handleDeleteMemory(item.id)}
                              className="text-[10px] text-rose-400 hover:text-rose-200 cursor-pointer"
                            >
                              Delete
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  ))
                )}
              </div>

              {/* Add Memory Form */}
              <div className="pt-2 border-t border-cyan-500/15 flex gap-2">
                <select
                  value={newMemCategory}
                  onChange={(e) => setNewMemCategory(e.target.value)}
                  className="px-2 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono w-28"
                >
                  <option value="fact">Fact</option>
                  <option value="preference">Preference</option>
                  <option value="concept">Concept</option>
                  <option value="task">Task</option>
                </select>
                <input
                  type="text"
                  placeholder="Enter memory statement or fact..."
                  value={newMemContent}
                  onChange={(e) => setNewMemContent(e.target.value)}
                  className="flex-1 px-2 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono"
                />
                <button
                  type="button"
                  onClick={() => void handleAddMemory()}
                  disabled={!newMemContent.trim()}
                  className="px-3 py-1 text-xs rounded bg-cyan-950 border border-cyan-400/40 text-cyan-300 hover:bg-cyan-900/60 transition cursor-pointer disabled:opacity-40"
                >
                  + Add Memory
                </button>
              </div>
            </section>
          )}

          {/* Privacy & Retention Controls Section */}
          {(activeCategory === "All" || activeCategory === "Privacy") && (
            <section className="settings-group p-3.5 rounded-xl border border-cyan-500/15 bg-slate-950/60 space-y-3">
              <div className="flex items-center justify-between border-b border-cyan-500/10 pb-1">
                <h3 className="text-xs font-bold text-cyan-400 uppercase tracking-wider flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-400" />
                  Storage Usage & Data Retention
                </h3>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-cyan-300 font-bold">
                    Total Usage: {privacySummary?.total_formatted || "Calculating..."}
                  </span>
                  <button
                    type="button"
                    onClick={() => void fetchPrivacySummary()}
                    disabled={privacyLoading}
                    className="text-[10px] text-cyan-300 hover:underline cursor-pointer"
                  >
                    {privacyLoading ? "Refreshing..." : "Refresh"}
                  </button>
                </div>
              </div>

              {/* Storage Breakdown Cards */}
              <div className="grid grid-cols-2 gap-2">
                {privacySummary &&
                  Object.entries(privacySummary.categories).map(([catKey, info]) => (
                    <div
                      key={catKey}
                      className="p-2 rounded bg-slate-900/60 border border-cyan-500/10 flex items-center justify-between"
                    >
                      <div>
                        <div className="text-[11px] font-bold text-slate-200">{info.name}</div>
                        <div className="text-[10px] text-slate-400">{info.formatted}</div>
                      </div>
                      {catKey !== "logs" && (
                        <button
                          type="button"
                          onClick={() => void handlePurge(catKey)}
                          className="px-2 py-0.5 text-[10px] rounded bg-rose-950/60 border border-rose-500/30 text-rose-300 hover:bg-rose-900/60 transition cursor-pointer"
                        >
                          Purge
                        </button>
                      )}
                    </div>
                  ))}
              </div>

              <div className="pt-2 flex justify-end">
                <button
                  type="button"
                  onClick={() => void handlePurge("all")}
                  className="px-3 py-1 text-xs rounded bg-rose-950/80 border border-rose-400/50 text-rose-200 hover:bg-rose-900/80 transition cursor-pointer"
                >
                  Purge All Stored Data
                </button>
              </div>
            </section>
          )}

          {/* Developer Diagnostics Section */}
          {(activeCategory === "All" || activeCategory === "Developer") && (
            <section className="settings-group p-3.5 rounded-xl border border-cyan-500/15 bg-slate-950/60 space-y-3">
              <div className="flex items-center justify-between border-b border-cyan-500/10 pb-1">
                <h3 className="text-xs font-bold text-cyan-400 uppercase tracking-wider flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-400" />
                  Developer Diagnostics & Live Telemetry
                </h3>
                <button
                  type="button"
                  onClick={() => void fetchDeveloperDiagnostics()}
                  disabled={devLoading}
                  className="text-[10px] text-cyan-300 hover:underline cursor-pointer"
                >
                  {devLoading ? "Refreshing..." : "Refresh"}
                </button>
              </div>

              {/* Metrics Grid */}
              <div className="grid grid-cols-3 gap-2 text-xs">
                <div className="p-2 rounded bg-slate-900/60 border border-cyan-500/10">
                  <span className="text-[10px] text-slate-400 block">Uptime</span>
                  <span className="text-cyan-300 font-bold">
                    {devDiagnostics?.system?.uptime_seconds ?? 0}s
                  </span>
                </div>
                <div className="p-2 rounded bg-slate-900/60 border border-cyan-500/10">
                  <span className="text-[10px] text-slate-400 block">Active Threads</span>
                  <span className="text-cyan-300 font-bold">
                    {devDiagnostics?.system?.active_threads ?? 0}
                  </span>
                </div>
                <div className="p-2 rounded bg-slate-900/60 border border-cyan-500/10">
                  <span className="text-[10px] text-slate-400 block">Active WebSockets</span>
                  <span className="text-cyan-300 font-bold">
                    {devDiagnostics?.system?.active_ws_connections ?? 0}
                  </span>
                </div>
              </div>

              {/* Capability Leases */}
              <div className="pt-2 border-t border-cyan-500/10">
                <div className="text-[11px] font-bold text-slate-300 mb-1">Active Capability Leases</div>
                {devDiagnostics?.leases && Object.keys(devDiagnostics.leases).length > 0 ? (
                  <div className="space-y-1">
                    {Object.entries(devDiagnostics.leases).map(([res, owner]) => (
                      <div
                        key={res}
                        className="px-2 py-1 rounded bg-slate-900 text-[10px] flex justify-between font-mono"
                      >
                        <span className="text-cyan-300">{res}</span>
                        <span className="text-slate-400">Owner: {owner}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-[10px] text-slate-500 italic">No exclusive leases held.</p>
                )}
              </div>

              {/* Recent Logs Tail */}
              <div className="pt-2 border-t border-cyan-500/10">
                <div className="text-[11px] font-bold text-slate-300 mb-1">Console Log Tail</div>
                <div className="p-2 rounded bg-black/80 border border-cyan-500/20 font-mono text-[10px] text-slate-300 h-32 overflow-y-auto space-y-0.5">
                  {devLogs.length === 0 ? (
                    <p className="text-slate-600 italic">No recent log entries.</p>
                  ) : (
                    devLogs.map((line, idx) => (
                      <div key={idx} className="truncate">
                        {line}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </section>
          )}

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
                    value={mapProviderMode}
                    onChange={(e) =>
                      setMapProviderMode(e.target.value as "hybrid" | "online" | "offline")
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
                      Adaptive visual effects and feature density
                    </small>
                  </span>
                  <select
                    className="px-2.5 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono w-44"
                    value={mapQuality}
                    onChange={(e) =>
                      setMapQuality(e.target.value as "auto" | "high" | "medium" | "low")
                    }
                  >
                    <option value="auto">Auto</option>
                    <option value="high">High Fidelity</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low / Fast</option>
                  </select>
                </label>

                <div className="flex flex-col gap-2 pt-1 border-t border-cyan-500/10">
                  <div className="flex items-center justify-between gap-4 text-slate-300">
                    <span className="max-w-[240px]">
                      <strong className="text-slate-200 font-mono text-[11px] block">
                        Discovered PMTiles Archives
                      </strong>
                      <small className="text-[10px] text-slate-400">
                        Select a verified dataset served via /api/geo/pmtiles/
                      </small>
                    </span>
                    <select
                      className="px-2.5 py-1 rounded bg-slate-900 border border-cyan-500/30 text-cyan-200 text-xs font-mono w-52"
                      value={mapPmtilesUrl || ""}
                      onChange={(e) => {
                        const val = e.target.value;
                        if (!val) {
                          setMapPmtilesUrl(null);
                        } else {
                          const archive = mapAvailableArchives.find((a) => a.url === val);
                          setMapPmtilesUrl(val, archive?.tileType || "vector", archive?.metadata);
                        }
                      }}
                      onFocus={() => fetchAvailableArchives()}
                    >
                      <option value="">-- None (Online Basemap) --</option>
                      {mapAvailableArchives.map((arch) => (
                        <option key={arch.url} value={arch.url}>
                          {arch.name} ({arch.tileType}, z{arch.minZoom}-{arch.maxZoom})
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
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
