"use client";

import { useEffect, useState, useMemo, type ReactElement } from "react";
import {
  Activity, Terminal, Shield, ChevronDown, ChevronUp, ListOrdered
} from "lucide-react";
import { useCharlieStore, rgba, groupMcpTools, type McpToolLike } from "../store/useCharlieStore";

interface ConfigField {
  key: string;
  value: unknown;
}

export function InsightRail(): ReactElement {
  const accentColor = useCharlieStore((s) => s.accentColor);
  const toolActivity = useCharlieStore((s) => s.toolActivity);
  const queue = useCharlieStore((s) => s.queue);

  // Active Model config representation
  const [configModel, setConfigModel] = useState("");
  const [visionModel, setVisionModel] = useState("");
  
  // Registered tools list -- toolsLoaded distinguishes "not fetched yet" from
  // "fetched, zero tools" so the panel doesn't render (0) while still loading.
  const [mcpTools, setMcpTools] = useState<McpToolLike[]>([]);
  const [toolsLoaded, setToolsLoaded] = useState(false);

  // Collapsible card sections state
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    agents: true,
    mcp: true,
    model: true,
  });

  const toggleSection = (key: string) => {
    setOpenSections((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  // Fetch registered tools (full registry, not just MCP) & model config,
  // then keep polling -- both can change while the dashboard is open
  // (extension installs, config edits), unlike a mount-only fetch.
  useEffect(() => {
    async function fetchData() {
      try {
        const rTools = await fetch("/api/tools");
        if (rTools.ok) {
          const data = await rTools.json();
          if (data.tools) setMcpTools(data.tools);
          setToolsLoaded(true);
        }
        const rConfig = await fetch("/api/config");
        if (rConfig.ok) {
          const data = await rConfig.json() as { fields?: ConfigField[] };
          const fields = data.fields || [];
          const llm = fields.find((f) => f.key === "LLM_MODEL");
          const vision = fields.find((f) => f.key === "VISION_LLM_MODEL");
          if (llm?.value) setConfigModel(String(llm.value));
          if (vision?.value) setVisionModel(String(vision.value));
        }
      } catch {
        // ignore
      }
    }
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, []);

  // Most recent tool activity first, capped to keep the panel scannable.
  const recentActivity = useMemo(() => toolActivity.slice(-8).reverse(), [toolActivity]);

  // Dynamic MCP server grouping & counts
  const mcpServers = useMemo(() => groupMcpTools(mcpTools), [mcpTools]);

  const accentDim = rgba(accentColor, 0.08);
  const accentBorder = rgba(accentColor, 0.25);

  return (
    <aside className="w-80 shrink-0 h-full border-l border-[var(--color-glass-border)] bg-zinc-950/40 flex flex-col p-4 space-y-4 overflow-y-auto scrollbar">
      
      {/* Queue: main.py's pending_turns -- utterances waiting behind an already-running turn */}
      {queue.count > 0 && (
        <div className="rounded-xl border border-status-warning/25 p-3.5 bg-status-warning/5">
          <span className="w-full flex items-center gap-1.5 text-[10px] font-bold text-status-warning uppercase tracking-widest">
            <ListOrdered className="w-3.5 h-3.5" />
            Queued ({queue.count})
          </span>
          <div className="space-y-1 pt-2">
            {queue.texts.map((t, idx) => (
              <p key={idx} className="text-[10px] text-slate-400 font-mono truncate">{t}</p>
            ))}
          </div>
        </div>
      )}

      {/* Widget 1: Live Activity -- real tool-call/thinking trace, not fabricated agent roles */}
      <div className="rounded-xl border border-[var(--color-glass-border)] p-3.5 bg-zinc-900/30">
        <button
          onClick={() => toggleSection("agents")}
          className="w-full flex items-center justify-between text-[10px] font-bold text-slate-500 uppercase tracking-widest cursor-pointer"
        >
          <span className="flex items-center gap-1.5">
            <Activity className="w-3.5 h-3.5 text-slate-400" />
            Live Activity
          </span>
          {openSections.agents ? <ChevronUp className="w-3.5 h-3.5 text-slate-400" /> : <ChevronDown className="w-3.5 h-3.5 text-slate-400" />}
        </button>

        {openSections.agents && (
          <div className="space-y-1.5 pt-3">
            {recentActivity.length === 0 ? (
              <p className="text-[10px] text-slate-500 font-mono py-1">No activity yet.</p>
            ) : (
              recentActivity.map((entry, idx) => (
                <div
                  key={`${entry.name}-${entry.kind}-${idx}`}
                  className="flex items-start gap-2 p-2 rounded-lg border border-transparent transition"
                  style={{
                    background: idx === 0 ? accentDim : "transparent",
                    borderColor: idx === 0 ? accentBorder : "transparent",
                  }}
                >
                  <span
                    className={`mt-1 w-1.5 h-1.5 rounded-full shrink-0 ${
                      idx === 0 ? "bg-status-success" : "bg-slate-700"
                    }`}
                  />
                  <div className="min-w-0 flex-1 font-mono text-[10px]">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-slate-200 font-bold truncate">{entry.name}</span>
                      <span className="text-slate-600 uppercase shrink-0">{entry.kind.replace("_", " ")}</span>
                    </div>
                    <p className="text-slate-500 truncate">{entry.text}</p>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {/* Widget 2: Registered MCP Server Tools */}
      <div className="rounded-xl border border-[var(--color-glass-border)] p-3.5 bg-zinc-900/30">
        <button
          onClick={() => toggleSection("mcp")}
          className="w-full flex items-center justify-between text-[10px] font-bold text-slate-500 uppercase tracking-widest cursor-pointer"
        >
          <span className="flex items-center gap-1.5">
            <Terminal className="w-3.5 h-3.5 text-slate-400" />
            Registered Tools ({toolsLoaded ? mcpTools.length : "..."})
          </span>
          {openSections.mcp ? <ChevronUp className="w-3.5 h-3.5 text-slate-400" /> : <ChevronDown className="w-3.5 h-3.5 text-slate-400" />}
        </button>

        {openSections.mcp && (
          <div className="space-y-1.5 pt-3">
            {!toolsLoaded ? (
              <p className="text-[10px] text-slate-500 font-mono py-1 animate-pulse">Querying registry...</p>
            ) : mcpServers.map((server) => (
              <div
                key={server.name}
                className="flex items-center justify-between p-2 rounded-lg bg-zinc-950/60 border border-white/5 font-mono text-xs"
              >
                <div className="flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-status-success" />
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

      {/* Widget 3: Model Status */}
      <div className="rounded-xl border border-[var(--color-glass-border)] p-3.5 bg-zinc-900/30">
        <button
          onClick={() => toggleSection("model")}
          className="w-full flex items-center justify-between text-[10px] font-bold text-slate-500 uppercase tracking-widest cursor-pointer"
        >
          <span className="flex items-center gap-1.5">
            <Shield className="w-3.5 h-3.5 text-slate-400" />
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
              <span className={`font-bold truncate max-w-[160px] ${visionModel ? "text-status-success" : "text-status-idle"}`}>
                {visionModel || "NOT CONFIGURED"}
              </span>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
