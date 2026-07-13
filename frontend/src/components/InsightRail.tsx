"use client";

import { useCallback, useEffect, useState } from "react";
import type { ReactElement } from "react";
import { useCharlieStore, rgba, lighten } from "../store/useCharlieStore";
import type { Task } from "../store/useCharlieStore";

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
  function?: { name: string; description?: string };
}

type Tab = "swarm" | "memory" | "mcp" | "tasks";

const TABS: { id: Tab; label: string }[] = [
  { id: "swarm", label: "Swarm" },
  { id: "memory", label: "Memory" },
  { id: "mcp", label: "MCP" },
  { id: "tasks", label: "Tasks" },
];

export const AGENT_COLOR: Record<string, string> = {
  "J.A.R.V.I.S.": "#3b82f6",
  "F.R.I.D.A.Y.": "#06b6d4",
  "Vision": "#8b5cf6",
  "E.D.I.T.H.": "#10b981",
  "A.I.D.A.": "#f59e0b",
  "Karen": "#ec4899",
  "H.E.R.B.I.E.": "#f97316",
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
  const accentColor = useCharlieStore((s) => s.accentColor);

  const accentDim = rgba(accentColor, 0.12);
  const accentBorder = rgba(accentColor, 0.25);
  const accentSoft = lighten(accentColor, 0.35);

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
                        style={{ backgroundColor: AGENT_COLOR[a.name] || "#4b5563" }}
                        className={`w-2 h-2 rounded-full shrink-0`}
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
