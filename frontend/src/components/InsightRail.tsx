"use client";

import { useCallback, useEffect, useState } from "react";
import type { ReactElement } from "react";
import { useCharlieStore, rgba, lighten } from "../store/useCharlieStore";
import type { Task } from "../store/useCharlieStore";
import { DesktopView } from "./DesktopView";
import catalogData from "../data/extensionCatalog.json";

interface Agent {
  name: string;
  status: string;
  current_task?: string;
  logs?: string[];
  token_cost?: number;
}
interface BlackboardState {
  tasks: Task[];
  agents: Record<string, Agent>;
}
interface SystemStatus {
  cpu: number;
  ram: number;
  gpu: number;
  active_agents: string[];
}

interface InsightRailProps {
  blackboard: BlackboardState | null;
  systemStatus: SystemStatus | null;
  onTerminateAgent?: (agentName: string) => void;
  onApproveTask?: (taskId: string) => void;
  onRejectTask?: (taskId: string, reason: string) => void;
  onCancelTask?: (taskId: string) => void;
  onRetryTask?: (taskId: string) => void;
}

interface Fact {
  subject: string;
  predicate: string;
  object: string;
}
interface McpTool {
  type: string;
  function?: {
    name: string;
    description?: string;
    parameters?: {
      type: string;
      properties?: Record<string, { type: string; description?: string }>;
      required?: string[];
    };
  };
}
interface ExtensionEntry {
  name: string;
  kind: string;
  source: string;
  enabled: boolean;
  tool_names: string[];
  warnings: string[];
  content_hash: string;
}
interface PendingProposal {
  pending_id: string;
  skill_card: string;
  warnings: string[];
}

type ExtensionKind = "mcp" | "skill" | "openapi" | "plugin";
const EXTENSION_KINDS: { id: ExtensionKind; label: string }[] = [
  { id: "plugin", label: "Built-in plugin" },
  { id: "mcp", label: "MCP server" },
  { id: "skill", label: "SKILL.md" },
  { id: "openapi", label: "OpenAPI spec" },
];

interface CatalogEntry {
  name: string;
  kind: ExtensionKind;
  description: string;
  source: string;
  rawText: string;
}
const CATALOG: CatalogEntry[] = catalogData.entries as CatalogEntry[];

type Tab = "swarm" | "memory" | "extensions" | "tasks" | "desktop";

const TABS: { id: Tab; label: string }[] = [
  { id: "swarm", label: "Swarm" },
  { id: "memory", label: "Memory" },
  { id: "extensions", label: "Extensions" },
  { id: "tasks", label: "Tasks" },
  { id: "desktop", label: "Desktop" },
];

export const AGENT_COLOR: Record<string, string> = {
  "J.A.R.V.I.S.": "#3b82f6",
  "Doctor Strange": "#8b5cf6",
  "Shuri": "#06b6d4",
  "E.D.I.T.H.": "#10b981",
  "K.A.R.E.N.": "#ec4899",
  "F.R.I.D.A.Y.": "#f59e0b",
  "Vision": "#f97316",
};

function statusColor(status: string): string {
  switch (status) {
    case "running":
    case "working":
      return "bg-[var(--color-accent-teal)] animate-pulse";
    case "done":
      return "bg-[#9ca3af]";
    case "failed":
      return "bg-[#ef4444]";
    default:
      return "bg-[#4b5563]";
  }
}

function EmptyState({ text }: { text: string }): ReactElement {
  return (
    <div className="h-full flex items-center justify-center px-6 text-center text-sm text-[var(--color-text-muted)]">
      {text}
    </div>
  );
}

interface GraphNode {
  id: string;
  x: number;
  y: number;
}
interface GraphEdge {
  from: string;
  to: string;
  label: string;
}

function buildGraph(facts: Fact[]): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const entities = new Map<string, number>();
  const edges: GraphEdge[] = [];
  for (const f of facts) {
    if (!entities.has(f.subject)) entities.set(f.subject, 0);
    if (!entities.has(f.object)) entities.set(f.object, 0);
    edges.push({ from: f.subject, to: f.object, label: f.predicate });
  }
  const ids = Array.from(entities.keys());
  const nodes: GraphNode[] = ids.map((id, i) => {
    const angle = (i / Math.max(ids.length, 1)) * Math.PI * 2 - Math.PI / 2;
    return {
      id,
      x: 160 + Math.cos(angle) * 130,
      y: 150 + Math.sin(angle) * 120,
    };
  });
  return { nodes, edges };
}

function MemoryGraph({ facts }: { facts: Fact[] }): ReactElement {
  const { nodes, edges } = buildGraph(facts);
  const pos = new Map(nodes.map((n) => [n.id, n]));
  return (
    <div className="rounded-2xl overflow-hidden border border-[var(--color-glass-border)] bg-[var(--color-glass-bg-2)]">
      <svg viewBox="0 0 320 300" className="w-full h-64 select-none">
        {edges.map((e, i) => {
          const a = pos.get(e.from);
          const b = pos.get(e.to);
          if (!a || !b) return null;
          const mx = (a.x + b.x) / 2;
          const my = (a.y + b.y) / 2;
          return (
            <g key={i}>
              <line
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke="var(--color-glass-border-hover)"
                strokeWidth="1"
              />
              <text
                x={mx}
                y={my}
                fill="var(--color-text-muted)"
                fontSize="7"
                textAnchor="middle"
                style={{ pointerEvents: "none" }}
              >
                {e.label.length > 18 ? e.label.slice(0, 17) + "…" : e.label}
              </text>
            </g>
          );
        })}
        {nodes.map((n) => (
          <g key={n.id}>
            <circle cx={n.x} cy={n.y} r="4" fill="var(--color-accent-teal)" />
            <text
              x={n.x}
              y={n.y - 8}
              fill="var(--color-text-secondary)"
              fontSize="8"
              textAnchor="middle"
              style={{ pointerEvents: "none" }}
            >
              {n.id.length > 14 ? n.id.slice(0, 13) + "…" : n.id}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

export function InsightRail({
  blackboard,
  systemStatus,
  onTerminateAgent,
  onApproveTask,
  onRejectTask,
  onCancelTask,
  onRetryTask,
}: InsightRailProps): ReactElement {
  const [tab, setTab] = useState<Tab>("swarm");
  const [facts, setFacts] = useState<Fact[]>([]);
  const [mcpTools, setMcpTools] = useState<McpTool[]>([]);
  const [factsLoaded, setFactsLoaded] = useState(false);
  const [toolsLoaded, setToolsLoaded] = useState(false);
  const [loadingFacts, setLoadingFacts] = useState(false);
  const [loadingTools, setLoadingTools] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [factSearch, setFactSearch] = useState("");
  const [mcpSearch, setMcpSearch] = useState("");
  const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>({});

  const [extensions, setExtensions] = useState<ExtensionEntry[]>([]);
  const [loadingExtensions, setLoadingExtensions] = useState(false);
  const [showInstallForm, setShowInstallForm] = useState(false);
  const [showCatalog, setShowCatalog] = useState(false);
  const [installKind, setInstallKind] = useState<ExtensionKind>("plugin");
  const [installName, setInstallName] = useState("");
  const [installSource, setInstallSource] = useState("");
  const [installRawText, setInstallRawText] = useState("");
  const [pendingProposal, setPendingProposal] = useState<PendingProposal | null>(null);
  const [installBusy, setInstallBusy] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);

  const loadJson = useCallback(
    async (
      url: string,
      onData: (data: { facts?: Fact[]; tools?: McpTool[]; extensions?: ExtensionEntry[] }) => void,
      setLoading: (v: boolean) => void,
      setLoaded: (v: boolean) => void
    ) => {
      setLoading(true);
      try {
        const r = await fetch(url);
        const d = (await r.json()) as { facts?: Fact[]; tools?: McpTool[] };
        onData(d);
      } catch {
        onData({});
      } finally {
        setLoading(false);
        setLoaded(true);
      }
    },
    []
  );

  const loadFacts = useCallback(async () => {
    await loadJson(
      "/api/memory/facts",
      (d) => setFacts(d.facts ?? []),
      setLoadingFacts,
      setFactsLoaded
    );
  }, [loadJson]);

  const loadTools = useCallback(async () => {
    await loadJson(
      "/api/mcp/tools",
      (d) => setMcpTools(d.tools ?? []),
      setLoadingTools,
      setToolsLoaded
    );
  }, [loadJson]);

  const loadExtensions = useCallback(async () => {
    await loadJson(
      "/api/extensions",
      (d) => setExtensions(d.extensions ?? []),
      setLoadingExtensions,
      () => {}
    );
  }, [loadJson]);

  useEffect(() => {
    if (tab === "memory") {
      void loadFacts();
    } else if (tab === "extensions") {
      void loadTools();
      void loadExtensions();
    }
  }, [tab, loadFacts, loadTools, loadExtensions]);

  const handlePropose = useCallback(async () => {
    setInstallError(null);
    setInstallBusy(true);
    try {
      const r = await fetch("/api/extensions/propose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind: installKind,
          name: installName,
          source: installSource,
          raw_text: installRawText,
        }),
      });
      const d = await r.json();
      if (d.status !== "ok") {
        setInstallError(d.message ?? "Propose failed");
        return;
      }
      setPendingProposal({ pending_id: d.pending_id, skill_card: d.skill_card, warnings: d.warnings ?? [] });
    } catch {
      setInstallError("Network error while proposing install");
    } finally {
      setInstallBusy(false);
    }
  }, [installKind, installName, installSource, installRawText]);

  const handleConfirm = useCallback(
    async (approved: boolean) => {
      if (!pendingProposal) return;
      setInstallBusy(true);
      try {
        const r = await fetch("/api/extensions/confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            pending_id: pendingProposal.pending_id,
            approved,
            kind: installKind,
            source: installSource,
            raw_text: installRawText,
          }),
        });
        const d = await r.json();
        if (d.status !== "ok") {
          setInstallError(d.message ?? "Confirm failed");
          return;
        }
        setPendingProposal(null);
        if (approved) {
          setInstallName("");
          setInstallSource("");
          setInstallRawText("");
          setShowInstallForm(false);
        }
        void loadExtensions();
      } catch {
        setInstallError("Network error while confirming install");
      } finally {
        setInstallBusy(false);
      }
    },
    [pendingProposal, installKind, installSource, installRawText, loadExtensions]
  );

  const handleToggleExtension = useCallback(
    async (name: string, enabled: boolean) => {
      await fetch(`/api/extensions/${encodeURIComponent(name)}/${enabled ? "disable" : "enable"}`, {
        method: "POST",
      });
      void loadExtensions();
    },
    [loadExtensions]
  );

  const handleUseCatalogEntry = useCallback((entry: CatalogEntry) => {
    setInstallKind(entry.kind);
    setInstallName(entry.name);
    setInstallSource(entry.source);
    setInstallRawText(entry.rawText);
    setShowCatalog(false);
    setShowInstallForm(true);
  }, []);

  const handleUninstallExtension = useCallback(
    async (name: string) => {
      await fetch(`/api/extensions/${encodeURIComponent(name)}`, { method: "DELETE" });
      void loadExtensions();
    },
    [loadExtensions]
  );

  const agents = blackboard?.agents ?? {};
  const tasks = blackboard?.tasks ?? [];
  const agentList = Object.values(agents);
  const accentColor = useCharlieStore((s) => s.accentColor);
  const desktopControlEnabled = useCharlieStore((s) => s.desktopControlEnabled);
  const visibleTabs = TABS.filter((t) => t.id !== "desktop" || desktopControlEnabled);

  const accentDim = rgba(accentColor, 0.12);
  const accentBorder = rgba(accentColor, 0.25);
  const accentSoft = lighten(accentColor, 0.35);

  return (
    <aside className="glass glass-hover anim-right flex flex-col w-80 shrink-0 h-full overflow-hidden rounded-2xl">
      {/* Segmented tab control */}
      <div className="px-3 pt-3">
        <div className="flex gap-1 rounded-2xl bg-[var(--color-glass-bg-2)] p-1 border border-[var(--color-glass-border)]">
          {visibleTabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              aria-label={t.label}
              style={{
                background: tab === t.id ? accentDim : "transparent",
                color: tab === t.id ? accentSoft : "#6b7280",
                borderColor: tab === t.id ? accentBorder : "transparent",
              }}
              className={`flex-1 rounded-xl py-2 text-xs font-medium transition cursor-pointer border`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Refresh trigger row */}
      {(tab === "memory" || tab === "extensions") && (
        <div className="px-4 pt-2 flex justify-end shrink-0 select-none">
          <button
            onClick={() => {
              if (tab === "memory") {
                void loadFacts();
              } else {
                void loadTools();
                void loadExtensions();
              }
            }}
            className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] hover:text-white transition cursor-pointer"
            disabled={loadingFacts || loadingTools || loadingExtensions}
          >
            <svg viewBox="0 0 24 24" className={`w-3 h-3 ${loadingFacts || loadingTools || loadingExtensions ? "animate-spin" : ""}`} fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
            </svg>
            <span>Refresh</span>
          </button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-4 scrollbar">
        {tab === "swarm" && (
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-2">
              {(["running", "pending", "done"] as const).map((st) => {
                const count = tasks.filter((t) => t.status === st).length;
                return (
                  <div
                    key={st}
                    className="rounded-xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] p-3 text-center"
                  >
                    <p className="font-mono text-xl font-bold text-[var(--color-text-primary)]">
                      {count}
                    </p>
                    <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                      {st}
                    </p>
                  </div>
                );
              })}
            </div>
 
            <div>
              <p className="text-xs uppercase tracking-widest text-[var(--color-text-muted)] mb-2">
                Active Agents
              </p>
              {agentList.length === 0 ? (
                <p className="text-sm text-[var(--color-text-muted)]">
                  No agents active.
                </p>
              ) : (
                <div className="space-y-2">
                  {agentList.map((a) => (
                    <button
                      key={a.name}
                      onClick={() => setSelectedAgent(a)}
                      className="w-full text-left flex items-center gap-3 rounded-xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] px-3 py-2 cursor-pointer transition hover:border-[var(--color-glass-border-hover)]"
                    >
                      <span
                        style={{
                          backgroundColor: AGENT_COLOR[a.name] || "#4b5563",
                          boxShadow: a.status === "running" ? `0 0 10px ${AGENT_COLOR[a.name] || "#4b5563"}` : "none",
                        }}
                        className={`w-2 h-2 rounded-full shrink-0 ${a.status === "running" ? "animate-pulse" : ""}`}
                        aria-hidden="true"
                      />
                      <div className="min-w-0">
                        <p className="text-sm text-[var(--color-text-primary)] truncate">
                          {a.name}
                        </p>
                        {a.status === "running" && (
                          <div className="flex items-center gap-1 mt-0.5">
                            <span className="flex h-1.5 w-1.5 relative">
                              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-purple-400 opacity-75"></span>
                              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-purple-500"></span>
                            </span>
                            <span className="text-[10px] text-purple-400 font-mono animate-pulse">Thinking...</span>
                          </div>
                        )}
                        {a.current_task && (
                          <p className="text-xs text-[var(--color-text-muted)] truncate mt-0.5">
                            {a.current_task}
                          </p>
                        )}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
 
        {tab === "memory" && (
          <div className="space-y-4">
            {loadingFacts ? (
              <EmptyState text="Loading memory graph..." />
            ) : (
              <>
                <MemoryGraph facts={facts} />
                
                <input
                  type="text"
                  value={factSearch}
                  onChange={(e) => setFactSearch(e.target.value)}
                  placeholder="Search facts..."
                  className="w-full bg-black/40 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-gray-500 focus:outline-none"
                />

                {facts.length === 0 ? (
                  <EmptyState text="No facts consolidated yet. Charlie builds its knowledge graph as you chat." />
                ) : (
                  (() => {
                    const filtered = facts.filter(f => 
                      f.subject.toLowerCase().includes(factSearch.toLowerCase()) ||
                      f.predicate.toLowerCase().includes(factSearch.toLowerCase()) ||
                      f.object.toLowerCase().includes(factSearch.toLowerCase())
                    );

                    return (
                      <div className="space-y-2 max-h-[220px] overflow-y-auto pr-1 scrollbar">
                        {filtered.length === 0 ? (
                          <p className="text-xs text-gray-500 font-mono py-4 text-center">No matching facts found.</p>
                        ) : (
                          filtered.map((f, i) => (
                            <div
                              key={i}
                              className="p-2 rounded-xl bg-white/5 border border-white/5 flex items-center justify-between text-[11px] hover:bg-white/10 group transition"
                            >
                              <div className="flex flex-wrap items-center gap-1.5 min-w-0 pr-2">
                                <span className="text-gray-300 font-semibold truncate max-w-[70px]" title={f.subject}>{f.subject}</span>
                                <span className="text-purple-400 text-[9px] font-mono px-1 bg-purple-500/10 rounded border border-purple-500/10">{f.predicate}</span>
                                <span className="text-gray-300 truncate max-w-[70px]" title={f.object}>{f.object}</span>
                              </div>
                              <button
                                onClick={async (e) => {
                                  e.stopPropagation();
                                  const res = await fetch(`/api/memory/facts?subject=${encodeURIComponent(f.subject)}&predicate=${encodeURIComponent(f.predicate)}&object=${encodeURIComponent(f.object)}`, {
                                    method: "DELETE"
                                  });
                                  if (res.ok) {
                                    void loadFacts();
                                  }
                                }}
                                className="text-gray-400 hover:text-red-400 opacity-0 group-hover:opacity-100 transition cursor-pointer"
                                title="Delete fact"
                              >
                                <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2">
                                  <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" />
                                </svg>
                              </button>
                            </div>
                          ))
                        )}
                      </div>
                    );
                  })()
                )}

                <p className="text-[10px] uppercase tracking-widest text-[var(--color-text-muted)] px-1 pt-1">
                  {facts.length} facts
                </p>
              </>
            )}
          </div>
        )}
 
        {tab === "extensions" && (
          <div className="space-y-4">
            <div>
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs uppercase tracking-widest text-[var(--color-text-muted)]">
                  Installed
                </p>
                <div className="flex gap-1">
                  <button
                    onClick={() => {
                      setShowCatalog((v) => !v);
                      setShowInstallForm(false);
                    }}
                    className="text-[10px] uppercase tracking-wider px-2 py-1 rounded-lg border border-[var(--color-glass-border)] text-[var(--color-text-secondary)] hover:text-white hover:border-[var(--color-glass-border-hover)] transition cursor-pointer"
                  >
                    {showCatalog ? "Hide catalog" : "Catalog"}
                  </button>
                  <button
                    onClick={() => {
                      setShowInstallForm((v) => !v);
                      setShowCatalog(false);
                    }}
                    className="text-[10px] uppercase tracking-wider px-2 py-1 rounded-lg border border-[var(--color-glass-border)] text-[var(--color-text-secondary)] hover:text-white hover:border-[var(--color-glass-border-hover)] transition cursor-pointer"
                  >
                    {showInstallForm ? "Cancel" : "+ Install"}
                  </button>
                </div>
              </div>

              {showCatalog && (
                <div className="rounded-xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] p-2 space-y-1.5 mb-3 max-h-[220px] overflow-y-auto scrollbar">
                  {CATALOG.map((entry) => (
                    <button
                      key={entry.name}
                      onClick={() => handleUseCatalogEntry(entry)}
                      className="w-full text-left rounded-lg px-2 py-1.5 hover:bg-white/5 transition cursor-pointer"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-xs font-semibold text-[var(--color-text-primary)]">{entry.name}</p>
                        <span className="text-[8px] font-semibold uppercase tracking-wider px-1 bg-purple-500/10 text-purple-400 border border-purple-500/20 rounded shrink-0">
                          {entry.kind}
                        </span>
                      </div>
                      <p className="text-[10px] text-[var(--color-text-muted)] mt-0.5">{entry.description}</p>
                    </button>
                  ))}
                </div>
              )}

              {showInstallForm && !pendingProposal && (
                <div className="rounded-xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] p-3 space-y-2 mb-3">
                  <div className="flex gap-1">
                    {EXTENSION_KINDS.map((k) => (
                      <button
                        key={k.id}
                        onClick={() => setInstallKind(k.id)}
                        className={`flex-1 text-[10px] py-1.5 rounded-lg border transition cursor-pointer ${
                          installKind === k.id
                            ? "border-[var(--color-accent-teal)] text-[var(--color-accent-teal)] bg-[var(--color-accent-teal)]/10"
                            : "border-[var(--color-glass-border)] text-[var(--color-text-muted)] hover:text-white"
                        }`}
                      >
                        {k.label}
                      </button>
                    ))}
                  </div>
                  <input
                    type="text"
                    value={installName}
                    onChange={(e) => setInstallName(e.target.value)}
                    placeholder={installKind === "plugin" ? "filesystem | browser | calendar | code_exec" : "Extension name"}
                    className="w-full bg-black/40 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-gray-500 focus:outline-none"
                  />
                  {installKind !== "plugin" && (
                    <input
                      type="text"
                      value={installSource}
                      onChange={(e) => setInstallSource(e.target.value)}
                      placeholder={installKind === "mcp" ? "Unused for MCP (name comes from spec)" : "Source URL (optional)"}
                      className="w-full bg-black/40 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-gray-500 focus:outline-none"
                    />
                  )}
                  {installKind !== "plugin" && (
                    <textarea
                      value={installRawText}
                      onChange={(e) => setInstallRawText(e.target.value)}
                      placeholder={
                        installKind === "mcp"
                          ? "name|command|arg1,arg2"
                          : installKind === "skill"
                            ? "Paste SKILL.md contents..."
                            : "Paste OpenAPI spec (JSON or YAML)..."
                      }
                      rows={4}
                      className="w-full bg-black/40 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-gray-500 focus:outline-none font-mono resize-none"
                    />
                  )}
                  {installError && <p className="text-[10px] text-red-400">{installError}</p>}
                  <button
                    onClick={() => void handlePropose()}
                    disabled={installBusy || !installName}
                    className="w-full py-1.5 rounded-lg bg-[var(--color-accent-teal)] hover:opacity-90 text-black text-[10px] font-semibold uppercase tracking-wider transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {installBusy ? "Checking..." : "Review install"}
                  </button>
                </div>
              )}

              {pendingProposal && (
                <div className="rounded-xl bg-[var(--color-glass-bg-2)] border border-amber-500/30 p-3 space-y-2 mb-3">
                  <p className="text-[10px] uppercase tracking-widest text-amber-400">
                    Approve this install?
                  </p>
                  <pre className="text-[10px] font-mono text-gray-300 whitespace-pre-wrap break-all bg-black/20 rounded-lg p-2 border border-white/5">
                    {pendingProposal.skill_card}
                  </pre>
                  {installError && <p className="text-[10px] text-red-400">{installError}</p>}
                  <div className="flex gap-2">
                    <button
                      onClick={() => void handleConfirm(false)}
                      disabled={installBusy}
                      className="flex-1 py-1.5 rounded-lg border border-red-500/30 hover:border-red-500 text-red-400 hover:bg-red-500/10 text-[10px] font-semibold uppercase tracking-wider transition cursor-pointer"
                    >
                      Decline
                    </button>
                    <button
                      onClick={() => void handleConfirm(true)}
                      disabled={installBusy}
                      className="flex-1 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white text-[10px] font-semibold uppercase tracking-wider transition cursor-pointer"
                    >
                      Approve
                    </button>
                  </div>
                </div>
              )}

              {loadingExtensions ? (
                <p className="text-xs text-[var(--color-text-muted)]">Loading extensions...</p>
              ) : extensions.length === 0 ? (
                <p className="text-xs text-[var(--color-text-muted)]">No extensions installed yet.</p>
              ) : (
                <div className="space-y-2">
                  {extensions.map((ext) => (
                    <div
                      key={ext.name}
                      className="rounded-xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] px-3 py-2"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span
                            className={`w-2 h-2 rounded-full shrink-0 ${ext.enabled ? "bg-emerald-400" : "bg-gray-500"}`}
                            aria-hidden="true"
                          />
                          <p className="text-xs font-semibold text-[var(--color-text-primary)] truncate">
                            {ext.name}
                          </p>
                          <span className="text-[8px] font-semibold uppercase tracking-wider px-1 bg-purple-500/10 text-purple-400 border border-purple-500/20 rounded shrink-0">
                            {ext.kind}
                          </span>
                        </div>
                        <div className="flex items-center gap-1 shrink-0">
                          <button
                            onClick={() => void handleToggleExtension(ext.name, ext.enabled)}
                            className="text-[9px] uppercase px-1.5 py-1 rounded-md border border-[var(--color-glass-border)] text-[var(--color-text-secondary)] hover:text-white transition cursor-pointer"
                          >
                            {ext.enabled ? "Disable" : "Enable"}
                          </button>
                          <button
                            onClick={() => void handleUninstallExtension(ext.name)}
                            title="Uninstall"
                            className="text-red-400 hover:text-red-300 transition cursor-pointer"
                          >
                            <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2">
                              <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" />
                            </svg>
                          </button>
                        </div>
                      </div>
                      {ext.tool_names.length > 0 && (
                        <p className="text-[10px] text-[var(--color-text-muted)] mt-1 font-mono truncate">
                          {ext.tool_names.join(", ")}
                        </p>
                      )}
                      {ext.warnings.length > 0 && (
                        <p className="text-[10px] text-amber-400 mt-1">
                          {ext.warnings.length} warning{ext.warnings.length > 1 ? "s" : ""} from install scan
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="border-t border-[var(--color-glass-border)] pt-3">
              <p className="text-xs uppercase tracking-widest text-[var(--color-text-muted)] mb-2">
                Registered tools
              </p>
            </div>

            <input
              type="text"
              value={mcpSearch}
              onChange={(e) => setMcpSearch(e.target.value)}
              placeholder="Search tools..."
              className="w-full bg-black/40 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-gray-500 focus:outline-none"
            />

            {loadingTools ? (
              <EmptyState text="Loading tools..." />
            ) : mcpTools.length === 0 ? (
              <EmptyState text="No tools registered." />
            ) : (
              (() => {
                const filtered = mcpTools.filter((t) => {
                  const name = t.function?.name ?? t.type;
                  const desc = t.function?.description ?? "";
                  return (
                    name.toLowerCase().includes(mcpSearch.toLowerCase()) ||
                    desc.toLowerCase().includes(mcpSearch.toLowerCase())
                  );
                });

                return (
                  <div className="space-y-2 max-h-[350px] overflow-y-auto pr-1 scrollbar">
                    {filtered.length === 0 ? (
                      <p className="text-xs text-gray-500 font-mono py-4 text-center">No matching tools found.</p>
                    ) : (
                      filtered.map((t, i) => {
                        const name = t.function?.name ?? t.type;
                        const isExpanded = expandedTools[name];
                        const serverName = name.startsWith("mcp_") ? name.split("_")[1] : "server";
                        
                        return (
                          <div
                            key={i}
                            onClick={() => setExpandedTools({ ...expandedTools, [name]: !isExpanded })}
                            className="rounded-xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] px-3 py-2 cursor-pointer hover:bg-white/[0.02] transition flex flex-col"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <p className="text-xs font-semibold text-[var(--color-text-primary)] font-mono truncate">
                                {name.replace(`mcp_${serverName}_`, "")}
                              </p>
                              <span className="text-[8px] font-semibold uppercase tracking-wider px-1 bg-purple-500/10 text-purple-400 border border-purple-500/20 rounded shrink-0">
                                {serverName}
                              </span>
                            </div>
                            
                            {t.function?.description && (
                              <p className="text-xs text-[var(--color-text-muted)] mt-0.5 line-clamp-2">
                                {t.function.description}
                              </p>
                            )}

                            {isExpanded && t.function?.parameters?.properties && (
                              <div
                                onClick={(e) => e.stopPropagation()}
                                className="mt-2 pt-2 border-t border-white/5 space-y-1.5 text-[10px] font-mono text-gray-400"
                              >
                                <p className="font-semibold text-gray-300">Parameters:</p>
                                {Object.entries(t.function.parameters.properties).map(([pName, pInfo]: [string, any]) => {
                                  const isRequired = t.function?.parameters?.required?.includes(pName);
                                  return (
                                    <div key={pName} className="flex flex-col bg-black/20 p-1.5 rounded border border-white/5">
                                      <div className="flex items-center justify-between">
                                        <span className="text-purple-300 font-bold">{pName}</span>
                                        <div className="flex gap-1.5">
                                          <span className="text-gray-500">[{pInfo.type}]</span>
                                          {isRequired && (
                                            <span className="text-red-400 text-[8px] bg-red-500/10 px-1 border border-red-500/20 rounded">
                                              Required
                                            </span>
                                          )}
                                        </div>
                                      </div>
                                      {pInfo.description && (
                                        <p className="text-[9px] text-gray-500 mt-0.5 leading-normal">
                                          {pInfo.description}
                                        </p>
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>
                );
              })()
            )}
          </div>
        )}

        {tab === "tasks" && (
          <div className="space-y-3">
            {tasks.length === 0 ? (
              <EmptyState text="No tasks on the board yet." />
            ) : (
              (() => {
                const doneTaskIds = new Set(tasks.filter((t) => t.status === "done").map((t) => t.id));
                return tasks.map((t) => {
                  const dotBg = t.assigned_to && AGENT_COLOR[t.assigned_to] 
                    ? AGENT_COLOR[t.assigned_to] 
                    : (t.status === "running" ? "var(--color-accent-teal)" : t.status === "done" ? "#10b981" : t.status === "failed" ? "#ef4444" : "#4b5563");
                  
                  const depsCount = t.dependencies ? t.dependencies.length : 0;
                  const depsReady = !t.dependencies || t.dependencies.every((depId) => doneTaskIds.has(depId));
                  
                  const priorities = ["Critical", "High", "Normal", "Low"];
                  const priorityVal = t.priority ?? 2;
                  const priorityLabel = priorities[priorityVal] || "Normal";
                  const priorityColor = priorityVal === 0 ? "text-red-400 bg-red-500/10 border-red-500/20" :
                                        priorityVal === 1 ? "text-orange-400 bg-orange-500/10 border-orange-500/20" :
                                        priorityVal === 2 ? "text-blue-400 bg-blue-500/10 border-blue-500/20" :
                                        "text-gray-400 bg-gray-500/10 border-gray-500/20";

                  return (
                    <div
                      key={t.id}
                      className="rounded-xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] p-3 flex flex-col gap-2 transition hover:bg-[var(--color-glass-bg-3)]"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span
                            style={{ backgroundColor: dotBg }}
                            className={`w-2.5 h-2.5 rounded-full shrink-0 ${t.status === "running" ? "animate-pulse" : ""}`}
                            aria-hidden="true"
                          />
                          <p className="text-sm font-medium text-[var(--color-text-primary)] truncate">
                            {t.name}
                          </p>
                        </div>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full border ${priorityColor} shrink-0`}>
                          {priorityLabel}
                        </span>
                      </div>

                      <div className="grid grid-cols-2 gap-1 text-[11px] text-[var(--color-text-muted)] border-t border-[var(--color-glass-border)] pt-2">
                        {t.assigned_to ? (
                          <div className="truncate">
                            <span className="text-[10px] text-gray-500">Agent:</span> <strong style={{ color: AGENT_COLOR[t.assigned_to] || "inherit" }}>{t.assigned_to}</strong>
                          </div>
                        ) : (
                          <div>
                            <span className="text-[10px] text-gray-500">Agent:</span> None
                          </div>
                        )}
                        <div className="truncate text-right">
                          <span className="text-[10px] text-gray-500">Deps:</span>{" "}
                          {depsCount === 0 ? (
                            <span className="text-emerald-400">None</span>
                          ) : depsReady ? (
                            <span className="text-emerald-400">Ready</span>
                          ) : (
                            <span className="text-amber-400">Blocked</span>
                          )}
                        </div>
                        <div>
                          <span className="text-[10px] text-gray-500">Retries:</span> {t.retry_count ?? 0}
                        </div>
                        <div className="truncate text-right">
                          <span className="text-[10px] text-gray-500">Approval:</span>{" "}
                          <span className={
                            (t.approval_status ?? "approved") === "approved" ? "text-emerald-400" :
                            (t.approval_status ?? "approved") === "rejected" ? "text-red-400" : "text-amber-400"
                          }>
                            {t.approval_status === "pending_approval" ? "Pending" : (t.approval_status ?? "approved")}
                          </span>
                        </div>
                      </div>

                      {t.result && (
                        <div className="text-[11px] px-2 py-1 rounded bg-black/20 border border-white/5 font-mono break-all max-h-16 overflow-y-auto text-gray-300">
                          {t.result}
                        </div>
                      )}

                      <div className="flex items-center justify-end gap-1.5 border-t border-[var(--color-glass-border)] pt-2 mt-1">
                        {t.approval_status === "pending_approval" && onApproveTask && onRejectTask && (
                          <>
                            <button
                              onClick={() => onRejectTask(t.id, "Rejected by user")}
                              className="px-2 py-1 text-[10px] font-medium rounded-lg border border-red-500/30 hover:border-red-500 text-red-400 hover:bg-red-500/10 transition cursor-pointer"
                            >
                              Reject
                            </button>
                            <button
                              onClick={() => onApproveTask(t.id)}
                              className="px-2 py-1 text-[10px] font-medium rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white transition cursor-pointer"
                            >
                              Approve
                            </button>
                          </>
                        )}

                        {t.status === "failed" && onRetryTask && (
                          <button
                            onClick={() => onRetryTask(t.id)}
                            title="Retry task"
                            className="px-2 py-1 text-[10px] font-medium rounded-lg border border-[var(--color-accent-teal)]/30 hover:border-[var(--color-accent-teal)] text-[var(--color-accent-teal)] hover:bg-[var(--color-accent-teal)]/10 transition cursor-pointer flex items-center gap-1"
                          >
                            <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2.5">
                              <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
                            </svg>
                            Retry
                          </button>
                        )}

                        {(t.status === "running" || t.status === "pending" || t.approval_status === "pending_approval") && onCancelTask && (
                          <button
                            onClick={() => onCancelTask(t.id)}
                            title="Cancel task"
                            className="px-2 py-1 text-[10px] font-medium rounded-lg border border-red-500/30 hover:border-red-500 text-red-400 hover:bg-red-500/10 transition cursor-pointer flex items-center gap-1"
                          >
                            <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2.5">
                              <path d="M18 6L6 18M6 6l12 12" />
                            </svg>
                            Cancel
                          </button>
                        )}
                      </div>
                    </div>
                  );
                });
              })()
            )}
          </div>
        )}

        {tab === "desktop" && <DesktopView />}
      </div>

      {systemStatus && (
        <div className="border-t border-[var(--color-glass-border)] px-4 py-3">
          <div className="flex items-center justify-between text-xs font-mono text-[var(--color-text-secondary)]">
            <span>CPU {systemStatus.cpu}%</span>
            <span>RAM {systemStatus.ram}%</span>
            <span>GPU {systemStatus.gpu}%</span>
          </div>
        </div>
      )}

      {/* Agent detail slide-over */}
      {selectedAgent && (
        <div className="absolute inset-0 z-30 flex justify-end">
          <div
            className="absolute inset-0 bg-black/45"
            onClick={() => setSelectedAgent(null)}
            aria-hidden="true"
          />
          <div className="relative w-[280px] h-full bg-black/88 backdrop-blur-[20px] border-l border-[var(--color-glass-border)] p-5 flex flex-col gap-4 anim-right">
            <div className="flex items-center justify-between">
              <h3 
                style={{ color: AGENT_COLOR[selectedAgent.name] || "var(--color-text-primary)" }}
                className="font-display text-base font-semibold"
              >
                {selectedAgent.name}
              </h3>
              <button
                onClick={() => setSelectedAgent(null)}
                aria-label="Close agent details"
                className="rounded-lg w-7 h-7 grid place-items-center text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] cursor-pointer"
              >
                <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>

            <div className="flex items-center gap-2">
              <span
                style={{ backgroundColor: AGENT_COLOR[selectedAgent.name] || "#4b5563" }}
                className={`w-2 h-2 rounded-full`}
                aria-hidden="true"
              />
              <span className="text-xs uppercase tracking-widest text-[var(--color-text-muted)] font-mono">
                {selectedAgent.status}
              </span>
            </div>

            {selectedAgent.current_task && (
              <div>
                <p className="text-[10px] uppercase tracking-widest text-[var(--color-text-muted)] mb-1 font-mono">
                  Current task
                </p>
                <p className="text-sm text-[var(--color-text-primary)]">
                  {selectedAgent.current_task}
                </p>
              </div>
            )}

            <div>
              <p className="text-[10px] uppercase tracking-widest text-[var(--color-text-muted)] mb-1 font-mono">
                Token cost
              </p>
              <p className="text-sm text-[var(--color-text-primary)] font-mono">
                {(selectedAgent.token_cost ?? 0).toFixed(2)}
              </p>
            </div>

            <div className="flex-1 min-h-0 flex flex-col">
              <p className="text-[10px] uppercase tracking-widest text-[var(--color-text-muted)] mb-1 font-mono">
                Activity log
              </p>
              <div className="flex-1 overflow-y-auto rounded-xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] p-2 scrollbar">
                {selectedAgent.logs && selectedAgent.logs.length > 0 ? (
                  selectedAgent.logs
                    .slice()
                    .reverse()
                    .map((line, i) => (
                      <p
                        key={i}
                        className="text-xs text-[var(--color-text-secondary)] font-mono leading-relaxed"
                      >
                        {line}
                      </p>
                    ))
                ) : (
                  <p className="text-xs text-[var(--color-text-muted)]">
                    No activity logged.
                  </p>
                )}
              </div>
            </div>

            {selectedAgent.status !== "idle" && onTerminateAgent && (
              <button
                onClick={() => {
                  onTerminateAgent(selectedAgent.name);
                  setSelectedAgent(null);
                }}
                className="w-full py-2.5 rounded-xl border border-[#ef4444]/40 hover:border-[#ef4444] text-[#ef4444] font-medium text-xs tracking-wider uppercase bg-[#ef4444]/5 hover:bg-[#ef4444]/10 transition cursor-pointer text-center"
              >
                Terminate Agent
              </button>
            )}
          </div>
        </div>
      )}
    </aside>
  );
}
