"use client";

import { useEffect, useState, useMemo, useCallback, type ReactElement } from "react";
import { 
  Database, Cpu, HardDrive, FileCode, CheckCircle, AlertCircle, Trash2, Search, Globe, Monitor, RefreshCw, Server
} from "lucide-react";
import { useCharlieStore } from "../store/useCharlieStore";

interface FactItem {
  id?: string;
  fact?: string;
  text?: string;
  created_at?: string;
}

interface LocalModelItem {
  name: string;
  source: string;
}

export function MemoriesView(): ReactElement {
  const [facts, setFacts] = useState<FactItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  const fetchFacts = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/memory/facts");
      if (res.ok) {
        const data = await res.json();
        setFacts(data.facts || []);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data fetch on mount
    fetchFacts();
  }, [fetchFacts]);

  const handleDeleteFact = async (factItem: FactItem) => {
    try {
      const res = await fetch("/api/memory/facts", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fact: factItem.fact || factItem.text || "", id: factItem.id }),
      });
      if (res.ok) {
        fetchFacts();
      }
    } catch {
      // ignore
    }
  };

  const filteredFacts = useMemo(() => {
    if (!search) return facts;
    return facts.filter((f) => 
      (f.fact || f.text || "").toLowerCase().includes(search.toLowerCase())
    );
  }, [facts, search]);

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
      <div className="border-b border-white/5 pb-3 flex justify-between items-end">
        <div>
          <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
            <Database className="w-5 h-5 text-purple-400" />
            Memories & Fact Consolidation
          </h2>
          <p className="text-xs text-slate-500 font-mono mt-1">
            ChromaDB & SQLite Vector Memory Recall
          </p>
        </div>

        <button
          onClick={fetchFacts}
          className="px-3 py-1.5 rounded-lg border border-white/10 bg-zinc-900/50 text-xs font-mono text-slate-300 hover:text-white flex items-center gap-1.5 cursor-pointer active:scale-95 transition"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-cyan-400" : ""}`} />
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="p-4 rounded-xl bg-zinc-900/40 border border-white/5 space-y-2">
          <span className="text-[10px] font-mono text-slate-500 uppercase font-bold">Consolidated Facts Count</span>
          <p className="text-2xl font-bold text-slate-100 font-mono">{facts.length}</p>
          <span className="text-[9px] text-emerald-400 block font-mono">Live ChromaDB triples</span>
        </div>
        <div className="p-4 rounded-xl bg-zinc-900/40 border border-white/5 space-y-2">
          <span className="text-[10px] font-mono text-slate-500 uppercase font-bold">Vector Storage Engine</span>
          <p className="text-2xl font-bold text-slate-100 font-mono">ChromaDB + SQLite</p>
          <span className="text-[9px] text-slate-500 block font-mono">FTS5 Full-Text Indexing</span>
        </div>
        <div className="p-4 rounded-xl bg-zinc-900/40 border border-white/5 space-y-2">
          <span className="text-[10px] font-mono text-slate-500 uppercase font-bold">Sync Schedule</span>
          <p className="text-2xl font-bold text-slate-100 font-mono">Every ~5 turns</p>
          <span className="text-[9px] text-cyan-400 block font-mono">Automatic memory consolidation</span>
        </div>
      </div>

      {/* Memory facts list */}
      <div className="rounded-xl border border-white/5 p-4 bg-zinc-900/20 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider font-mono">
            Active Consolidated Memory Facts ({filteredFacts.length})
          </h3>
          <div className="relative w-64">
            <Search className="absolute left-2.5 top-2 w-3.5 h-3.5 text-slate-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search facts..."
              className="w-full bg-zinc-900 border border-white/10 rounded-lg pl-8 pr-3 py-1 text-xs text-slate-200 placeholder:text-slate-500 outline-none"
            />
          </div>
        </div>

        {loading ? (
          <div className="py-8 text-center text-xs font-mono text-slate-500 animate-pulse">
            Fetching facts from memory store...
          </div>
        ) : filteredFacts.length === 0 ? (
          <div className="py-8 text-center text-xs font-mono text-slate-500 italic border border-dashed border-white/5 rounded-xl">
            No consolidated memory facts found. Facts are automatically saved every ~5 conversation turns.
          </div>
        ) : (
          <div className="space-y-2 max-h-80 overflow-y-auto pr-1 scrollbar font-mono text-xs">
            {filteredFacts.map((item, idx) => (
              <div key={idx} className="p-3 rounded-lg bg-zinc-950/60 border border-white/5 flex items-start justify-between gap-4">
                <div className="space-y-1">
                  <p className="text-slate-200 leading-relaxed">{item.fact || item.text}</p>
                  {item.created_at && (
                    <span className="text-[9px] text-slate-500 block">{item.created_at}</span>
                  )}
                </div>
                <button
                  onClick={() => handleDeleteFact(item)}
                  className="p-1 rounded text-slate-500 hover:text-red-400 hover:bg-white/5 transition"
                  title="Delete fact"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

interface HostStatus {
  cpu?: number;
  ram?: number;
  gpu?: number;
  launch_id?: string;
  desktop_control_enabled?: boolean;
}

export function HardwareView(): ReactElement {
  const storeStatus = useCharlieStore((s) => s.systemStatus);
  const [status, setStatus] = useState<HostStatus | null>(null);
  const [ping, setPing] = useState<number | null>(null);

  const fetchStatus = useCallback(async () => {
    const start = Date.now();
    try {
      const res = await fetch("/api/status");
      if (res.ok) {
        const data = await res.json();
        setStatus(data);
        setPing(Date.now() - start);
      }
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data fetch on mount
    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  const liveStatus: HostStatus = status || storeStatus;

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
      <div className="border-b border-white/5 pb-3">
        <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
          <Cpu className="w-5 h-5 text-cyan-400" />
          Hardware & Telemetry
        </h2>
        <p className="text-xs text-slate-500 font-mono mt-1">
          Real-Time Host System & Browser Environment Specs
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Host Hardware Metrics */}
        <div className="rounded-xl border border-white/5 p-4 bg-zinc-900/20 space-y-4">
          <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider font-mono flex items-center gap-2">
            <HardDrive className="w-4 h-4 text-cyan-400" />
            Host Server System Stats
          </h3>
          <div className="space-y-2.5 font-mono text-xs text-slate-400">
            <div className="flex justify-between">
              <span>CPU LOAD</span>
              <span className="text-slate-200 font-bold">{liveStatus.cpu?.toFixed?.(1) ?? 0}%</span>
            </div>
            <div className="flex justify-between">
              <span>RAM USAGE</span>
              <span className="text-slate-200 font-bold">{liveStatus.ram?.toFixed?.(1) ?? 0}%</span>
            </div>
            <div className="flex justify-between">
              <span>GPU MEMORY USAGE</span>
              <span className="text-slate-200 font-bold">{liveStatus.gpu?.toFixed?.(1) ?? 0}%</span>
            </div>
            <div className="flex justify-between border-t border-white/5 pt-2">
              <span>PROCESS LAUNCH ID</span>
              <span className="text-purple-400 font-semibold truncate max-w-[140px]">{liveStatus.launch_id || "Standalone"}</span>
            </div>
            <div className="flex justify-between">
              <span>DESKTOP CONTROL STATE</span>
              <span className={liveStatus.desktop_control_enabled ? "text-emerald-400" : "text-slate-500"}>
                {liveStatus.desktop_control_enabled ? "ENABLED" : "DISABLED"}
              </span>
            </div>
          </div>
        </div>

        {/* Browser Telemetry */}
        <div className="rounded-xl border border-white/5 p-4 bg-zinc-900/20 space-y-4">
          <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider font-mono flex items-center gap-2">
            <Monitor className="w-4 h-4 text-cyan-400" />
            Client Environment Telemetry
          </h3>
          <div className="space-y-2.5 font-mono text-xs text-slate-400">
            <div className="flex justify-between">
              <span>HTTP API PING LATENCY</span>
              <span className="text-emerald-400 font-bold">{ping !== null ? `${ping} ms` : "Measuring..."}</span>
            </div>
            <div className="flex justify-between">
              <span>SCREEN RESOLUTION</span>
              <span className="text-slate-200">
                {typeof window !== "undefined" ? `${window.screen.width}x${window.screen.height}` : "1920x1080"} px
              </span>
            </div>
            <div className="flex justify-between">
              <span>VIEWPORT DIMENSIONS</span>
              <span className="text-slate-200">
                {typeof window !== "undefined" ? `${window.innerWidth}x${window.innerHeight}` : "1280x720"} px
              </span>
            </div>
            <div className="flex justify-between border-t border-white/5 pt-2">
              <span>DEVICE PIXEL RATIO</span>
              <span className="text-slate-200">{typeof window !== "undefined" ? window.devicePixelRatio : 1}x</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function FilesView(): ReactElement {
  const setSelectedFileContent = useCharlieStore((s) => s.setSelectedFileContent);
  const [fileList, setFileList] = useState<string[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchFileList = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/workspace/files");
      if (res.ok) {
        const data = await res.json();
        setFileList(data.files || []);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data fetch on mount
    fetchFileList();
  }, [fetchFileList]);

  const selectFile = async (filePath: string) => {
    setSelectedFile(filePath);
    try {
      const res = await fetch(`/api/workspace/file?path=${encodeURIComponent(filePath)}`);
      if (res.ok) {
        const data = await res.json();
        setSelectedFileContent(data.content || "");
      }
    } catch {
      setSelectedFileContent("// Failed to load file content.");
    }
  };

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out] select-none">
      <div className="border-b border-white/5 pb-3 flex justify-between items-end">
        <div>
          <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
            <FileCode className="w-5 h-5 text-purple-400" />
            Workspace File Tree Explorer
          </h2>
          <p className="text-xs text-slate-500 font-mono mt-1">
            Real Workspace Source Files (Click to load inside Console ARTIFACTS tab)
          </p>
        </div>

        <button
          onClick={fetchFileList}
          className="px-3 py-1.5 rounded-lg border border-white/10 bg-zinc-900/50 text-xs font-mono text-slate-300 hover:text-white flex items-center gap-1.5 cursor-pointer active:scale-95 transition"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-purple-400" : ""}`} />
          Scan Workspace
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Workspace Tree */}
        <div className="rounded-xl border border-white/5 p-4 bg-zinc-900/20 font-mono text-xs space-y-3 max-h-[460px] overflow-y-auto scrollbar">
          <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider font-mono sticky top-0 bg-zinc-950/90 py-1">
            workspace/ ({fileList.length} files)
          </h3>

          {loading ? (
            <div className="py-8 text-center text-xs font-mono text-slate-500 animate-pulse">
              Scanning workspace files...
            </div>
          ) : (
            <div className="space-y-1">
              {fileList.map((file) => (
                <button
                  key={file}
                  onClick={() => selectFile(file)}
                  className={`w-full text-left py-1 px-2 rounded hover:bg-white/5 transition flex items-center gap-2 cursor-pointer font-mono truncate ${
                    selectedFile === file ? "text-cyan-400 bg-white/5 font-semibold" : "text-slate-400"
                  }`}
                >
                  <FileCode className="w-3.5 h-3.5 shrink-0" />
                  <span className="truncate">{file}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Selected file info card */}
        <div className="p-4 rounded-xl border border-white/5 bg-zinc-900/40 flex flex-col justify-center text-center space-y-2">
          {selectedFile ? (
            <>
              <CheckCircle className="w-8 h-8 text-emerald-400 mx-auto" />
              <p className="text-sm font-bold text-slate-200 font-mono truncate px-4">
                {selectedFile}
              </p>
              <p className="text-xs text-slate-400 max-w-xs mx-auto leading-relaxed">
                Loaded live source code into the bottom console&apos;s **ARTIFACTS** tab. Switch to the artifacts tab at the bottom to read full file content.
              </p>
            </>
          ) : (
            <>
              <AlertCircle className="w-8 h-8 text-slate-600 mx-auto" />
              <p className="text-xs text-slate-500 font-mono">
                No file selected. Click a workspace file in the tree to read its content.
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

interface ServiceStatus {
  type: string;
  status: string;
  name: string;
  details: string;
}

export function DockerView(): ReactElement {
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchServices = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/services/status");
      if (res.ok) {
        const json = await res.json();
        setServices(json.services || []);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data fetch on mount
    fetchServices();
  }, [fetchServices]);

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
      <div className="border-b border-white/5 pb-3 flex justify-between items-end">
        <div>
          <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
            <Server className="w-5 h-5 text-purple-400" />
            Active Subprocesses & Services
          </h2>
          <p className="text-xs text-slate-500 font-mono mt-1">
            Real Running Charlie System Subprocesses & ZeroMQ IPC Channels
          </p>
        </div>

        <button
          onClick={fetchServices}
          className="px-3 py-1.5 rounded-lg border border-white/10 bg-zinc-900/50 text-xs font-mono text-slate-300 hover:text-white flex items-center gap-1.5 cursor-pointer active:scale-95 transition"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-purple-400" : ""}`} />
          Query Services
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 font-mono text-xs">
        {services.map((s, i) => (
          <div key={i} className="p-4 rounded-xl bg-zinc-900/40 border border-white/5 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-mono text-slate-500 uppercase font-bold">{s.type}</span>
              <span className="text-[8px] uppercase font-bold text-emerald-400 font-mono bg-emerald-950/40 border border-emerald-500/20 px-1.5 py-0.5 rounded">
                {s.status}
              </span>
            </div>
            <p className="text-xs font-bold text-slate-200 truncate">{s.name}</p>
            <p className="text-[10px] text-slate-400 leading-relaxed">{s.details}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

export function OllamaView(): ReactElement {
  const [data, setData] = useState<{ count: number; models: LocalModelItem[] }>({ count: 0, models: [] });
  const [loading, setLoading] = useState(true);

  const fetchLocalModels = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/local_models");
      if (res.ok) {
        const json = await res.json();
        setData({
          count: json.count || 0,
          models: json.models || [],
        });
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data fetch on mount
    fetchLocalModels();
  }, [fetchLocalModels]);

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
      <div className="border-b border-white/5 pb-3 flex justify-between items-end">
        <div>
          <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
            <Globe className="w-5 h-5 text-cyan-400" />
            Strictly Local Models Telemetry
          </h2>
          <p className="text-xs text-slate-500 font-mono mt-1">
            Local Ollama (11434) and LM Studio (1234) Server Endpoints (Cloud API Keys Excluded)
          </p>
        </div>

        <button
          onClick={fetchLocalModels}
          className="px-3 py-1.5 rounded-lg border border-white/10 bg-zinc-900/50 text-xs font-mono text-slate-300 hover:text-white flex items-center gap-1.5 cursor-pointer active:scale-95 transition"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-cyan-400" : ""}`} />
          Query Endpoints
        </button>
      </div>

      <div className="rounded-xl border border-white/5 p-4 bg-zinc-900/20 space-y-4 font-mono text-xs text-slate-400">
        <div className="flex justify-between">
          <span>LOCAL ENDPOINTS</span>
          <span className="text-slate-200 font-bold">Ollama (:11434), LM Studio (:1234)</span>
        </div>
        <div className="flex justify-between">
          <span>DISCOVERY STATE</span>
          <span className={data.count > 0 ? "text-emerald-400 font-bold uppercase" : "text-amber-400 font-bold uppercase"}>
            {data.count > 0 ? "ACTIVE LOCAL MODELS" : "NO LOCAL SERVERS RUNNING"}
          </span>
        </div>
        <div className="space-y-2 border-t border-white/5 pt-3">
          <span className="text-[10px] text-slate-500 uppercase tracking-wider block font-bold">
            AVAILABLE LOCALLY HOSTED MODELS ({data.models.length})
          </span>
          {data.models.length === 0 ? (
            <p className="text-slate-500 italic py-4">
              No local model servers detected at http://127.0.0.1:11434 (Ollama) or http://127.0.0.1:1234 (LM Studio). Start your local server to load local weights.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2 pt-1">
              {data.models.map((m, i) => (
                <div key={i} className="px-3 py-1.5 rounded-lg bg-zinc-900 border border-white/10 flex items-center gap-2">
                  <span className="text-cyan-300 font-bold text-xs">{m.name}</span>
                  <span className="text-[9px] text-slate-500 bg-white/5 px-1.5 py-0.5 rounded font-mono">{m.source}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
