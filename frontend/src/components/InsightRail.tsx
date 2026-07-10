"use client";

import { useCallback, useEffect, useState } from "react";
import type { ReactElement } from "react";

interface Agent {
  name: string;
  status: string;
  current_task?: string;
  logs?: string[];
  token_cost?: number;
}
interface Task {
  id: string;
  name: string;
  status: string;
  assigned_to?: string;
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
}

interface Fact {
  subject: string;
  predicate: string;
  object: string;
}
interface McpTool {
  type: string;
  function?: { name: string; description?: string };
}

type Tab = "swarm" | "memory" | "mcp" | "tasks";

const TABS: { id: Tab; label: string }[] = [
  { id: "swarm", label: "Swarm" },
  { id: "memory", label: "Memory" },
  { id: "mcp", label: "MCP" },
  { id: "tasks", label: "Tasks" },
];

function statusColor(status: string): string {
  switch (status) {
    case "running":
    case "working":
      return "bg-status-listening";
    case "done":
      return "bg-status-speaking";
    case "failed":
      return "bg-status-error";
    default:
      return "bg-status-idle";
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
}: InsightRailProps): ReactElement {
  const [tab, setTab] = useState<Tab>("swarm");
  const [facts, setFacts] = useState<Fact[]>([]);
  const [mcpTools, setMcpTools] = useState<McpTool[]>([]);
  const [factsLoaded, setFactsLoaded] = useState(false);
  const [toolsLoaded, setToolsLoaded] = useState(false);
  const [loadingFacts, setLoadingFacts] = useState(false);
  const [loadingTools, setLoadingTools] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);

  const loadJson = useCallback(
    async (
      url: string,
      onData: (data: { facts?: Fact[]; tools?: McpTool[] }) => void,
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

  useEffect(() => {
    if (tab === "memory" && !factsLoaded && !loadingFacts) {
      setTimeout(() => {
        void loadJson(
          "/api/memory/facts",
          (d) => setFacts(d.facts ?? []),
          setLoadingFacts,
          setFactsLoaded
        );
      }, 0);
    }
    if (tab === "mcp" && !toolsLoaded && !loadingTools) {
      setTimeout(() => {
        void loadJson(
          "/api/mcp/tools",
          (d) => setMcpTools(d.tools ?? []),
          setLoadingTools,
          setToolsLoaded
        );
      }, 0);
    }
  }, [tab, factsLoaded, toolsLoaded, loadingFacts, loadingTools, loadJson]);

  const agents = blackboard?.agents ?? {};
  const tasks = blackboard?.tasks ?? [];
  const agentList = Object.values(agents);

  return (
    <aside className="glass glass-hover anim-right flex flex-col w-80 shrink-0 h-full overflow-hidden rounded-2xl">
      {/* Segmented tab control */}
      <div className="px-3 pt-3">
        <div className="flex gap-1 rounded-2xl bg-[var(--color-glass-bg-2)] p-1 border border-[var(--color-glass-border)]">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              aria-label={t.label}
              className={`flex-1 rounded-xl py-2 text-xs font-medium transition cursor-pointer ${
                tab === t.id
                  ? "bg-[var(--color-accent-teal-dim)] text-[var(--color-accent-teal-soft)] border border-[var(--color-accent-teal)]/20"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] border border-transparent"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

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
                        className={`w-2 h-2 rounded-full shrink-0 ${statusColor(a.status)}`}
                        aria-hidden="true"
                      />
                      <div className="min-w-0">
                        <p className="text-sm text-[var(--color-text-primary)] truncate">
                          {a.name}
                        </p>
                        {a.current_task && (
                          <p className="text-xs text-[var(--color-text-muted)] truncate">
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
          <div className="space-y-2">
            {loadingFacts ? (
              <EmptyState text="Loading memory graph..." />
            ) : facts.length === 0 ? (
              <EmptyState text="No facts consolidated yet. Charlie builds its knowledge graph as you chat." />
            ) : (
              <>
                <MemoryGraph facts={facts} />
                <p className="text-[10px] uppercase tracking-widest text-[var(--color-text-muted)] px-1 pt-1">
                  {facts.length} facts
                </p>
              </>
            )}
          </div>
        )}

        {tab === "mcp" && (
          <div className="space-y-2">
            {loadingTools ? (
              <EmptyState text="Loading tools..." />
            ) : mcpTools.length === 0 ? (
              <EmptyState text="No tools registered." />
            ) : (
              mcpTools.map((t, i) => (
                <div
                  key={i}
                  className="rounded-xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] px-3 py-2"
                >
                  <p className="text-sm text-[var(--color-text-primary)] font-mono truncate">
                    {t.function?.name ?? t.type}
                  </p>
                  {t.function?.description && (
                    <p className="text-xs text-[var(--color-text-muted)] truncate">
                      {t.function.description}
                    </p>
                  )}
                </div>
              ))
            )}
          </div>
        )}

        {tab === "tasks" && (
          <div className="space-y-2">
            {tasks.length === 0 ? (
              <EmptyState text="No tasks on the board yet." />
            ) : (
              tasks.map((t) => (
                <div
                  key={t.id}
                  className="flex items-center justify-between gap-3 rounded-xl bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] px-3 py-2"
                >
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <span
                      className={`w-2 h-2 rounded-full shrink-0 ${statusColor(t.status)}`}
                      aria-hidden="true"
                    />
                    <div className="min-w-0">
                      <p className="text-sm text-[var(--color-text-primary)] truncate">
                        {t.name}
                      </p>
                      {t.assigned_to && (
                        <p className="text-xs text-[var(--color-text-muted)] truncate">
                          {t.assigned_to}
                        </p>
                      )}
                    </div>
                  </div>
                  {t.status === "running" && t.assigned_to && onTerminateAgent && (
                    <button
                      onClick={() => onTerminateAgent(t.assigned_to!)}
                      title={`Cancel task (terminate ${t.assigned_to})`}
                      className="p-1 rounded-lg border border-[#ef4444]/30 hover:border-[#ef4444] text-[#ef4444] hover:bg-[#ef4444]/10 transition cursor-pointer shrink-0"
                    >
                      <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                        <path d="M18 6L6 18M6 6l12 12" />
                      </svg>
                    </button>
                  )}
                </div>
              ))
            )}
          </div>
        )}
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
            className="absolute inset-0 bg-black/40"
            onClick={() => setSelectedAgent(null)}
            aria-hidden="true"
          />
          <div className="relative w-72 h-full glass rounded-l-2xl border-l border-[var(--color-glass-border)] p-5 flex flex-col gap-4 anim-right">
            <div className="flex items-center justify-between">
              <h3 className="font-display text-base font-semibold text-[var(--color-text-primary)]">
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
                className={`w-2 h-2 rounded-full ${statusColor(selectedAgent.status)}`}
                aria-hidden="true"
              />
              <span className="text-xs uppercase tracking-widest text-[var(--color-text-muted)]">
                {selectedAgent.status}
              </span>
            </div>

            {selectedAgent.current_task && (
              <div>
                <p className="text-[10px] uppercase tracking-widest text-[var(--color-text-muted)] mb-1">
                  Current task
                </p>
                <p className="text-sm text-[var(--color-text-primary)]">
                  {selectedAgent.current_task}
                </p>
              </div>
            )}

            <div>
              <p className="text-[10px] uppercase tracking-widest text-[var(--color-text-muted)] mb-1">
                Token cost
              </p>
              <p className="text-sm text-[var(--color-text-primary)] font-mono">
                {(selectedAgent.token_cost ?? 0).toFixed(2)}
              </p>
            </div>

            <div className="flex-1 min-h-0 flex flex-col">
              <p className="text-[10px] uppercase tracking-widest text-[var(--color-text-muted)] mb-1">
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
