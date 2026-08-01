"use client";

import { useEffect, useState, useMemo, type ReactElement } from "react";
import {
  Users, Terminal, Shield, Play, ChevronDown, ChevronUp
} from "lucide-react";
import { useCharlieStore, rgba } from "../store/useCharlieStore";

interface McpTool {
  type: string;
  function?: {
    name: string;
    description?: string;
  };
}

interface InsightRailProps {
  onStartBackgroundTask: (text: string) => void;
  onCancelBackgroundTask: (taskId: string) => void;
}

export function InsightRail({
  onStartBackgroundTask,
  onCancelBackgroundTask,
}: InsightRailProps): ReactElement {
  const accentColor = useCharlieStore((s) => s.accentColor);
  const toolActivity = useCharlieStore((s) => s.toolActivity);
  const voiceState = useCharlieStore((s) => s.voiceState);
  const backgroundTask = useCharlieStore((s) => s.backgroundTask);
  const messagesLoading = useCharlieStore((s) => s.messagesLoading);

  // Active Model config representation
  const [configModel, setConfigModel] = useState("");
  const [visionModel, setVisionModel] = useState("");
  
  // Registered tools list
  const [mcpTools, setMcpTools] = useState<McpTool[]>([]);

  // Collapsible card sections state
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    agents: true,
    mcp: true,
    workflows: true,
    model: true,
  });

  const toggleSection = (key: string) => {
    setOpenSections((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  // State timers for active roles
  const [roleTimes, setRoleTimes] = useState<Record<string, number>>({
    Planner: 0,
    Researcher: 0,
    Developer: 0,
    Reporter: 0,
  });

  // Fetch registered tools & model config (once on mount)
  useEffect(() => {
    async function fetchData() {
      try {
        const rTools = await fetch("/api/mcp/tools");
        if (rTools.ok) {
          const data = await rTools.json();
          if (data.tools) setMcpTools(data.tools);
        }
        const rConfig = await fetch("/api/config");
        if (rConfig.ok) {
          const data = await rConfig.json();
          if (data.llm_model) setConfigModel(data.llm_model);
          if (data.vision_llm_model) setVisionModel(data.vision_llm_model);
        }
      } catch {
        // ignore
      }
    }
    fetchData();
  }, []);

  // Determine active roles dynamically
  const activeRoles = useMemo(() => {
    const roles = {
      Planner: false,
      Researcher: false,
      Developer: false,
      Reporter: false,
    };

    if (voiceState === "speaking" || (messagesLoading && toolActivity.length === 0)) {
      roles.Reporter = true;
    }

    if (toolActivity.length > 0) {
      const latest = toolActivity[toolActivity.length - 1];
      const name = latest.name.toLowerCase();
      
      if (name.includes("plan") || name.includes("think") || name.includes("consolidate") || latest.kind === "thinking_update") {
        roles.Planner = true;
      } else if (name.includes("search") || name.includes("web") || name.includes("fetch") || name.includes("read_url") || name.includes("permission") || name.includes("view_file")) {
        roles.Researcher = true;
      } else if (name.includes("command") || name.includes("run") || name.includes("write") || name.includes("replace") || name.includes("git") || name.includes("task") || name.includes("edit")) {
        roles.Developer = true;
      } else {
        roles.Planner = true;
      }
    }

    return roles;
  }, [voiceState, toolActivity, messagesLoading]);

  // Role timer interval
  useEffect(() => {
    const timer = setInterval(() => {
      setRoleTimes((prev) => {
        const next = { ...prev };
        (Object.keys(activeRoles) as Array<keyof typeof activeRoles>).forEach((role) => {
          if (activeRoles[role]) {
            next[role] = (next[role] || 0) + 1;
          }
        });
        return next;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [activeRoles]);

  // Dynamic MCP server grouping & counts
  const mcpServers = useMemo(() => {
    const servers: Record<string, number> = {};
    mcpTools.forEach((tool) => {
      const name = tool.function?.name ?? tool.type;
      if (name.startsWith("mcp_")) {
        const parts = name.split("_");
        const server = parts[1];
        servers[server] = (servers[server] || 0) + 1;
      } else {
        servers["local"] = (servers["local"] || 0) + 1;
      }
    });
    return Object.entries(servers).map(([name, count]) => ({
      name,
      count,
      status: "active",
    }));
  }, [mcpTools]);

  const workflowPresets = [
    { label: "Security Assessment Plan", prompt: "Prepare a complete penetration testing assessment plan for a local web node." },
    { label: "Refactor codebase", prompt: "Sweep the directory and outline unused code imports or layout blocks." },
    { label: "System Health Audit", prompt: "Conduct a full check of open sockets, system PID count, and system services health." }
  ];

  const accentDim = rgba(accentColor, 0.08);
  const accentBorder = rgba(accentColor, 0.25);

  return (
    <aside className="w-80 shrink-0 h-full border-l border-[rgba(255,255,255,0.07)] bg-zinc-950/40 flex flex-col p-4 space-y-4 overflow-y-auto scrollbar select-none">
      
      {/* Widget 1: Agent Live Feed */}
      <div className="rounded-xl border border-[rgba(255,255,255,0.07)] p-3.5 bg-zinc-900/30">
        <button
          onClick={() => toggleSection("agents")}
          className="w-full flex items-center justify-between text-[9px] font-bold text-slate-500 uppercase tracking-widest font-mono cursor-pointer"
        >
          <span className="flex items-center gap-1.5">
            <Users className="w-3.5 h-3.5 text-purple-400" />
            Agent Live Feed
          </span>
          {openSections.agents ? <ChevronUp className="w-3.5 h-3.5 text-slate-400" /> : <ChevronDown className="w-3.5 h-3.5 text-slate-400" />}
        </button>

        {openSections.agents && (
          <div className="space-y-2 pt-3">
            {["Planner", "Researcher", "Developer", "Reporter"].map((role) => {
              const active = activeRoles[role as keyof typeof activeRoles];
              const elapsed = roleTimes[role];
              return (
                <div
                  key={role}
                  className="flex items-center justify-between p-2 rounded-lg border border-transparent transition"
                  style={{
                    background: active ? accentDim : "transparent",
                    borderColor: active ? accentBorder : "transparent",
                  }}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={`w-2 h-2 rounded-full ${
                        active ? "bg-emerald-400 animate-pulse" : "bg-slate-700"
                      }`}
                    />
                    <span className="text-xs font-semibold text-slate-200">{role}</span>
                  </div>

                  <div className="flex items-center gap-2 font-mono text-[10px]">
                    <span className={active ? "text-cyan-400 font-bold" : "text-slate-500"}>
                      {active ? "ACTIVE" : "IDLE"}
                    </span>
                    <span className="text-slate-600 font-bold">
                      {elapsed > 0 ? `${elapsed}s` : "0s"}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Widget 2: Registered MCP Server Tools */}
      <div className="rounded-xl border border-[rgba(255,255,255,0.07)] p-3.5 bg-zinc-900/30">
        <button
          onClick={() => toggleSection("mcp")}
          className="w-full flex items-center justify-between text-[9px] font-bold text-slate-500 uppercase tracking-widest font-mono cursor-pointer"
        >
          <span className="flex items-center gap-1.5">
            <Terminal className="w-3.5 h-3.5 text-cyan-400" />
            Registered Tools ({mcpTools.length})
          </span>
          {openSections.mcp ? <ChevronUp className="w-3.5 h-3.5 text-slate-400" /> : <ChevronDown className="w-3.5 h-3.5 text-slate-400" />}
        </button>

        {openSections.mcp && (
          <div className="space-y-1.5 pt-3">
            {mcpServers.map((server) => (
              <div
                key={server.name}
                className="flex items-center justify-between p-2 rounded-lg bg-zinc-950/60 border border-white/5 font-mono text-xs"
              >
                <div className="flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                  <span className="text-slate-300 font-bold capitalize">{server.name}</span>
                </div>
                <span className="text-[10px] text-slate-400 font-bold bg-white/5 px-2 py-0.5 rounded">
                  {server.count} tools
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Widget 3: Active Workflows Stepper */}
      <div className="rounded-xl border border-[rgba(255,255,255,0.07)] p-3.5 bg-zinc-900/30">
        <button
          onClick={() => toggleSection("workflows")}
          className="w-full flex items-center justify-between text-[9px] font-bold text-slate-500 uppercase tracking-widest font-mono cursor-pointer"
        >
          <span className="flex items-center gap-1.5">
            <Play className="w-3.5 h-3.5 text-purple-400" />
            Running Workflows
          </span>
          {openSections.workflows ? <ChevronUp className="w-3.5 h-3.5 text-slate-400" /> : <ChevronDown className="w-3.5 h-3.5 text-slate-400" />}
        </button>

        {openSections.workflows && (
          <div className="pt-3">
            {backgroundTask && !["done", "failed", "cancelled"].includes(backgroundTask.status) ? (
              <div className="space-y-2 p-2.5 rounded-lg bg-zinc-950/60 border border-white/5 text-xs font-mono">
                <div className="flex items-center justify-between">
                  <span className="text-cyan-400 font-bold uppercase text-[10px]">{backgroundTask.status}</span>
                  <button
                    onClick={() => onCancelBackgroundTask(backgroundTask.id)}
                    className="text-[9px] text-red-400 hover:bg-white/5 px-1 rounded cursor-pointer"
                  >
                    Cancel
                  </button>
                </div>
                <p className="text-slate-200 truncate">{backgroundTask.text}</p>
                <div className="space-y-1 pl-2 border-l border-white/10 pt-1">
                  {backgroundTask.steps.map((step, idx) => (
                    <div key={idx} className="flex items-center gap-1.5 text-[10px]">
                      <span className={`w-1.5 h-1.5 rounded-full ${idx <= backgroundTask.current_step ? "bg-emerald-400" : "bg-slate-700"}`} />
                      <span className={idx === backgroundTask.current_step ? "text-slate-100 font-bold" : "text-slate-500"}>
                        {step}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-1.5">
                {workflowPresets.map((wf, idx) => (
                  <button
                    key={idx}
                    onClick={() => onStartBackgroundTask(wf.prompt)}
                    className="w-full text-left p-2 rounded-lg bg-zinc-950/40 border border-white/5 hover:bg-white/5 text-xs font-mono text-slate-300 hover:text-slate-100 transition cursor-pointer flex items-center justify-between group"
                  >
                    <span className="truncate pr-2">{wf.label}</span>
                    <Play className="w-3 h-3 text-slate-500 group-hover:text-cyan-400 shrink-0" />
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Widget 4: Model Status */}
      <div className="rounded-xl border border-[rgba(255,255,255,0.07)] p-3.5 bg-zinc-900/30">
        <button
          onClick={() => toggleSection("model")}
          className="w-full flex items-center justify-between text-[9px] font-bold text-slate-500 uppercase tracking-widest font-mono cursor-pointer"
        >
          <span className="flex items-center gap-1.5">
            <Shield className="w-3.5 h-3.5 text-cyan-400" />
            Model Status
          </span>
          {openSections.model ? <ChevronUp className="w-3.5 h-3.5 text-slate-400" /> : <ChevronDown className="w-3.5 h-3.5 text-slate-400" />}
        </button>

        {openSections.model && (
          <div className="space-y-2 pt-3 font-mono text-xs text-slate-400">
            <div className="flex justify-between items-center p-2 rounded-lg bg-zinc-950/60 border border-white/5">
              <span>ACTIVE MODEL</span>
              <span className="text-purple-400 font-bold truncate max-w-[160px]">{configModel || "—"}</span>
            </div>
            <div className="flex justify-between items-center p-2 rounded-lg bg-zinc-950/60 border border-white/5 text-[10px]">
              <span>VISION MODEL</span>
              <span className={`font-bold truncate max-w-[160px] ${visionModel ? "text-emerald-400" : "text-slate-500"}`}>
                {visionModel || "NOT CONFIGURED"}
              </span>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
