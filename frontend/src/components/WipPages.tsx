"use client";

import { useEffect, useState, useMemo, useCallback, type ReactElement } from "react";
import {
  Database, Cpu, HardDrive, FileCode, AlertCircle, Trash2, Globe, Monitor, RefreshCw, Server, Puzzle, Plus, Power, Save,
  GitBranch, Circle, CheckCircle2, XCircle, Clock, X, Cable
} from "lucide-react";
import { useCharlieStore, type AgentRun, type McpToolLike, groupMcpTools } from "../store/useCharlieStore";
import { Button } from "./Button";

interface LocalModelItem {
  name: string;
  source: string;
  active: boolean;
  size_bytes: number | null;
  parameter_size: string | null;
  quantization: string | null;
  context_length: number | null;
  loaded_in_vram: boolean | null;
  vram_bytes: number | null;
}

interface LocalEndpointItem {
  name: string;
  url: string;
  reachable: boolean;
  latency_ms: number | null;
}

function formatBytes(bytes: number | null | undefined): string {
  if (!bytes) return "";
  const gb = bytes / (1024 * 1024 * 1024);
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(bytes / (1024 * 1024)).toFixed(0)} MB`;
}

const MEMORY_FILES = [
  { path: "MEMORY.md", label: "Memory" },
  { path: "USER.md", label: "User" },
  { path: "OPINIONS.md", label: "Opinions" },
  { path: "PROJECT.md", label: "Project" },
] as const;

export function MemoriesView(): ReactElement {
  const [contents, setContents] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const results = await Promise.all(
        MEMORY_FILES.map(async ({ path }) => {
          try {
            const res = await fetch(`/api/workspace/file?path=${encodeURIComponent(path)}`);
            if (res.ok) return [path, (await res.json()).content as string] as const;
          } catch {
            // ignore
          }
          return [path, ""] as const;
        })
      );
      setContents(Object.fromEntries(results));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data fetch on mount
    fetchAll();
  }, [fetchAll]);

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
      <div className="border-b border-white/5 pb-3 flex justify-between items-end">
        <div>
          <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
            <Database className="w-5 h-5 text-slate-400" />
            Memories
          </h2>
          <p className="text-xs text-slate-500 font-mono mt-1">
            Markdown memory files -- edit via the Files page, updated live by the `memory` tool
          </p>
        </div>
        <Button onClick={fetchAll} className="font-mono">
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-cyan-400" : ""}`} />
          Refresh
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {MEMORY_FILES.map(({ path, label }) => (
          <div key={path} className="rounded-xl border border-white/5 p-4 bg-zinc-900/20 space-y-2">
            <span className="text-[10px] font-mono text-slate-500 uppercase font-bold">{label} ({path})</span>
            {loading ? (
              <p className="text-xs font-mono text-slate-500 animate-pulse py-4">Loading...</p>
            ) : contents[path] ? (
              <pre className="text-[10px] font-mono text-slate-300 whitespace-pre-wrap max-h-80 overflow-y-auto scrollbar">
                {contents[path]}
              </pre>
            ) : (
              <p className="text-[10px] font-mono text-slate-500 italic py-4">Empty.</p>
            )}
          </div>
        ))}
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

  // /api/status carries launch_id/desktop_control_enabled but not cpu/ram/gpu --
  // those only arrive over the WS system_status event, into storeStatus. Merge
  // rather than replace so the fetch doesn't blank out the live telemetry.
  const liveStatus: HostStatus = { ...storeStatus, ...status };

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
      <div className="border-b border-white/5 pb-3">
        <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
          <Cpu className="w-5 h-5 text-slate-400" />
          Hardware & Telemetry
        </h2>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Host Hardware Metrics */}
        <div className="rounded-xl border border-white/5 p-4 bg-zinc-900/20 space-y-4">
          <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider font-mono flex items-center gap-2">
            <HardDrive className="w-4 h-4 text-slate-400" />
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
            <Monitor className="w-4 h-4 text-slate-400" />
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
  const [editText, setEditText] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

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
    setError("");
    try {
      const res = await fetch(`/api/workspace/file?path=${encodeURIComponent(filePath)}`);
      if (res.ok) {
        const data = await res.json();
        setSelectedFileContent(data.content || "");
        setEditText(data.content || "");
        setDirty(false);
      }
    } catch {
      setSelectedFileContent("// Failed to load file content.");
    }
  };

  const handleSave = async () => {
    if (!selectedFile) return;
    setSaving(true);
    setError("");
    try {
      const res = await fetch("/api/workspace/file", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: selectedFile, content: editText }),
      });
      if (res.ok) {
        setSelectedFileContent(editText);
        setDirty(false);
      } else {
        setError("Save failed");
      }
    } catch {
      setError("Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!selectedFile) return;
    setError("");
    try {
      const res = await fetch(`/api/workspace/file?path=${encodeURIComponent(selectedFile)}`, {
        method: "DELETE",
      });
      if (res.ok) {
        setSelectedFile(null);
        setEditText("");
        setSelectedFileContent("");
        setDirty(false);
        fetchFileList();
      } else {
        setError("Delete failed");
      }
    } catch {
      setError("Delete failed");
    }
  };

  const [newFileOpen, setNewFileOpen] = useState(false);
  const [newFilePath, setNewFilePath] = useState("");

  const handleNewFile = async () => {
    const path = newFilePath.trim();
    if (!path) return;
    setError("");
    try {
      const res = await fetch("/api/workspace/file", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, content: "" }),
      });
      if (res.ok) {
        setNewFilePath("");
        setNewFileOpen(false);
        await fetchFileList();
        selectFile(path);
      } else {
        setError("Create failed (allowed extensions: .py .md .json .css .ts .tsx .js .html)");
      }
    } catch {
      setError("Create failed");
    }
  };

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
      <div className="border-b border-white/5 pb-3 flex justify-between items-end">
        <div>
          <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
            <FileCode className="w-5 h-5 text-slate-400" />
            Workspace File Tree Explorer
          </h2>
          <p className="text-xs text-slate-500 font-mono mt-1">
            Click a file to edit it below
          </p>
        </div>

        <div className="flex gap-2">
          <Button onClick={() => setNewFileOpen((v) => !v)} className="font-mono">
            <Plus className="w-3.5 h-3.5" />
            New File
          </Button>
          <Button onClick={fetchFileList} className="font-mono">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-purple-400" : ""}`} />
            Scan Workspace
          </Button>
        </div>
      </div>

      {newFileOpen && (
        <div className="flex gap-2 font-mono text-xs">
          <input
            value={newFilePath}
            onChange={(e) => setNewFilePath(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleNewFile();
              if (e.key === "Escape") setNewFileOpen(false);
            }}
            placeholder="path/relative/to/workspace/root.md"
            autoFocus
            className="flex-1 bg-zinc-950 border border-white/10 rounded-lg px-2 py-1.5 text-slate-200 placeholder:text-slate-600"
          />
          <Button onClick={handleNewFile} disabled={!newFilePath.trim()}>Create</Button>
          <Button onClick={() => setNewFileOpen(false)}>Cancel</Button>
        </div>
      )}

      {error && <p className="text-xs text-red-400 font-mono">{error}</p>}

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

        {/* Selected file editor */}
        <div className="rounded-xl border border-white/5 bg-zinc-900/40 flex flex-col max-h-[460px]">
          {selectedFile ? (
            <>
              <div className="flex items-center justify-between px-3 py-2 border-b border-white/5">
                <span className="text-xs font-bold text-slate-200 font-mono truncate">{selectedFile}</span>
                <div className="flex gap-2 shrink-0">
                  <Button size="sm" onClick={handleSave} disabled={!dirty || saving}>
                    <Save className="w-3 h-3" /> {saving ? "Saving..." : "Save"}
                  </Button>
                  <Button size="sm" variant="danger" onClick={handleDelete}>
                    <Trash2 className="w-3 h-3" /> Delete
                  </Button>
                </div>
              </div>
              <textarea
                value={editText}
                onChange={(e) => {
                  setEditText(e.target.value);
                  setDirty(true);
                }}
                spellCheck={false}
                className="flex-1 w-full bg-zinc-950/60 text-slate-300 font-mono text-[11px] leading-relaxed p-3 outline-none resize-none scrollbar"
              />
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-center space-y-2 p-4">
              <AlertCircle className="w-8 h-8 text-slate-600 mx-auto" />
              <p className="text-xs text-slate-500 font-mono">
                No file selected. Click a workspace file in the tree to edit it.
              </p>
            </div>
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

interface DockerContainer {
  Names?: string;
  Image?: string;
  Status?: string;
  Ports?: string;
}

export function ServicesView(): ReactElement {
  const [services, setServices] = useState<ServiceStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [dockerAvailable, setDockerAvailable] = useState(false);
  const [containers, setContainers] = useState<DockerContainer[]>([]);
  const [dockerLoading, setDockerLoading] = useState(true);

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

  const fetchDocker = useCallback(async () => {
    setDockerLoading(true);
    try {
      const res = await fetch("/api/docker/status");
      if (res.ok) {
        const json = await res.json();
        setDockerAvailable(Boolean(json.available));
        setContainers(json.containers || []);
      }
    } catch {
      // ignore
    } finally {
      setDockerLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data fetch on mount
    fetchServices();
    fetchDocker();
  }, [fetchServices, fetchDocker]);

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
      <div className="border-b border-white/5 pb-3 flex justify-between items-end">
        <div>
          <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
            <Server className="w-5 h-5 text-slate-400" />
            Active Subprocesses & Services
          </h2>
        </div>

        <Button
          onClick={() => {
            fetchServices();
            fetchDocker();
          }}
          className="font-mono"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading || dockerLoading ? "animate-spin text-purple-400" : ""}`} />
          Query Services
        </Button>
      </div>

      {loading && services.length === 0 ? (
        <div className="py-8 text-center text-xs font-mono text-slate-500 animate-pulse">
          Querying subprocess & service status...
        </div>
      ) : services.length === 0 ? (
        <div className="py-8 text-center text-xs font-mono text-slate-500 italic border border-dashed border-white/5 rounded-xl">
          No services reported.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 font-mono text-xs">
          {services.map((s, i) => (
            <div key={i} className="p-4 rounded-xl bg-zinc-900/40 border border-white/5 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-mono text-slate-500 uppercase font-bold">{s.type}</span>
                <span
                  className={`text-[10px] uppercase font-bold font-mono px-1.5 py-0.5 rounded border ${
                    s.status === "online"
                      ? "text-emerald-400 bg-emerald-950/40 border-emerald-500/20"
                      : "text-slate-500 bg-zinc-950/60 border-white/10"
                  }`}
                >
                  {s.status}
                </span>
              </div>
              <p className="text-xs font-bold text-slate-200 truncate">{s.name}</p>
              <p className="text-[10px] text-slate-400 leading-relaxed">{s.details}</p>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-3">
        <h3 className="text-xs font-bold text-slate-300 uppercase tracking-wider font-mono border-t border-white/5 pt-4">
          Docker Containers
        </h3>
        {dockerLoading ? (
          <div className="py-8 text-center text-xs font-mono text-slate-500 animate-pulse">
            Querying Docker daemon...
          </div>
        ) : !dockerAvailable ? (
          <div className="py-8 text-center text-xs font-mono text-slate-500 italic border border-dashed border-white/5 rounded-xl">
            Docker daemon not reachable (`docker ps` failed or Docker isn&apos;t installed).
          </div>
        ) : containers.length === 0 ? (
          <div className="py-8 text-center text-xs font-mono text-slate-500 italic border border-dashed border-white/5 rounded-xl">
            Docker is running, no containers active.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 font-mono text-xs">
            {containers.map((c, i) => (
              <div key={i} className="p-4 rounded-xl bg-zinc-900/40 border border-white/5 space-y-2">
                <p className="text-xs font-bold text-slate-200 truncate">{c.Names || "unnamed"}</p>
                <p className="text-[10px] text-slate-400 truncate">{c.Image}</p>
                <p className="text-[10px] text-slate-500">{c.Status}</p>
                {c.Ports && <p className="text-[10px] text-slate-500 truncate">{c.Ports}</p>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function OllamaView(): ReactElement {
  const [data, setData] = useState<{ count: number; models: LocalModelItem[]; activeModel: string; activeIsLocal: boolean; endpoints: LocalEndpointItem[] }>({
    count: 0, models: [], activeModel: "", activeIsLocal: false, endpoints: [],
  });
  const [loading, setLoading] = useState(true);
  const [pullName, setPullName] = useState("");
  const [pulling, setPulling] = useState(false);
  const [busyModel, setBusyModel] = useState<string | null>(null);
  const [error, setError] = useState("");

  const fetchLocalModels = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/local_models");
      if (res.ok) {
        const json = await res.json();
        setData({
          count: json.count || 0,
          models: json.models || [],
          activeModel: json.active_model || "",
          activeIsLocal: Boolean(json.active_is_local),
          endpoints: json.endpoints || [],
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

  const handlePull = async () => {
    if (!pullName.trim()) return;
    setPulling(true);
    setError("");
    try {
      const res = await fetch("/api/local_models/pull", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: pullName.trim() }),
      });
      if (res.ok) {
        setPullName("");
        fetchLocalModels();
      } else {
        setError("Pull failed -- check the model name and that Ollama is running.");
      }
    } catch {
      setError("Pull request failed.");
    } finally {
      setPulling(false);
    }
  };

  const handleDeleteModel = async (name: string) => {
    setBusyModel(name);
    setError("");
    try {
      const res = await fetch(`/api/local_models/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (res.ok) {
        fetchLocalModels();
      } else {
        setError(`Delete failed for ${name}.`);
      }
    } catch {
      setError(`Delete request failed for ${name}.`);
    } finally {
      setBusyModel(null);
    }
  };

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
      <div className="border-b border-white/5 pb-3 flex justify-between items-end">
        <div>
          <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
            <Globe className="w-5 h-5 text-slate-400" />
            Strictly Local Models Telemetry
          </h2>
          <p className="text-xs text-slate-500 font-mono mt-1">
            Local Ollama (11434) and LM Studio (1234) Server Endpoints (Cloud API Keys Excluded)
          </p>
        </div>

        <Button onClick={fetchLocalModels} className="font-mono">
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-cyan-400" : ""}`} />
          Query Endpoints
        </Button>
      </div>

      <div className={`rounded-xl border p-4 space-y-1 font-mono text-xs ${data.activeIsLocal ? "border-emerald-500/30 bg-emerald-500/5" : "border-amber-500/30 bg-amber-500/5"}`}>
        <span className="text-[10px] uppercase tracking-wider block font-bold text-slate-500">Charlie&apos;s Active LLM</span>
        {data.activeIsLocal ? (
          <p className="text-emerald-400">
            <span className="font-bold">{data.activeModel}</span> -- serving from a local endpoint below.
          </p>
        ) : (
          <p className="text-amber-400">
            <span className="font-bold">{data.activeModel || "(not configured)"}</span> -- not one of the local endpoints below (cloud/remote LLM_URL, or no local server matches this name).
          </p>
        )}
      </div>

      <div className="rounded-xl border border-white/5 p-4 bg-zinc-900/20 space-y-3 font-mono text-xs">
        <span className="text-[10px] text-slate-500 uppercase tracking-wider block font-bold">Pull Ollama Model</span>
        <div className="flex gap-2">
          <input
            value={pullName}
            onChange={(e) => setPullName(e.target.value)}
            placeholder="model name (e.g. llama3.1:8b)"
            className="flex-1 bg-zinc-950 border border-white/10 rounded-lg px-2 py-1.5 text-slate-200 placeholder:text-slate-600"
          />
          <Button onClick={handlePull} disabled={pulling || !pullName.trim()}>
            <Plus className="w-3.5 h-3.5" />
            {pulling ? "Pulling..." : "Pull"}
          </Button>
        </div>
        {error && <p className="text-red-400">{error}</p>}
      </div>

      <div className="rounded-xl border border-white/5 p-4 bg-zinc-900/20 space-y-4 font-mono text-xs text-slate-400">
        <div className="space-y-1.5">
          <span className="text-[10px] text-slate-500 uppercase tracking-wider block font-bold">Local Endpoints</span>
          {data.endpoints.map((ep) => (
            <div key={ep.name} className="flex justify-between items-center">
              <span className="text-slate-300">{ep.name} <span className="text-slate-500">({ep.url})</span></span>
              {ep.reachable ? (
                <span className="text-emerald-400 font-bold">REACHABLE -- {ep.latency_ms}ms</span>
              ) : (
                <span className="text-amber-400 font-bold">UNREACHABLE</span>
              )}
            </div>
          ))}
        </div>
        <div className="space-y-2 border-t border-white/5 pt-3">
          <span className="text-[10px] text-slate-500 uppercase tracking-wider block font-bold">
            AVAILABLE LOCALLY HOSTED MODELS ({data.models.length})
          </span>
          {loading ? (
            <p className="text-slate-500 italic py-4 animate-pulse">Querying local endpoints...</p>
          ) : data.models.length === 0 ? (
            <p className="text-slate-500 italic py-4">
              No local model servers detected at http://127.0.0.1:11434 (Ollama) or http://127.0.0.1:1234 (LM Studio). Start your local server to load local weights.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2 pt-1">
              {data.models.map((m, i) => (
                <div
                  key={i}
                  className={`px-3 py-1.5 rounded-lg border flex items-center gap-2 ${m.active ? "bg-emerald-500/10 border-emerald-500/40" : "bg-zinc-900 border-white/10"}`}
                >
                  {m.active && (
                    <span className="text-[9px] text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded font-mono font-bold uppercase">In use</span>
                  )}
                  <span className="text-cyan-300 font-bold text-xs">{m.name}</span>
                  <span className="text-[10px] text-slate-500 bg-white/5 px-1.5 py-0.5 rounded font-mono">{m.source}</span>
                  {m.parameter_size && (
                    <span className="text-[10px] text-slate-400">{m.parameter_size}</span>
                  )}
                  {m.quantization && (
                    <span className="text-[10px] text-slate-400 bg-white/5 px-1.5 py-0.5 rounded">{m.quantization}</span>
                  )}
                  {m.context_length && (
                    <span className="text-[10px] text-slate-400">{m.context_length.toLocaleString()} ctx</span>
                  )}
                  {formatBytes(m.size_bytes) && (
                    <span className="text-[10px] text-slate-500">{formatBytes(m.size_bytes)}</span>
                  )}
                  {m.loaded_in_vram && (
                    <span className="text-[9px] text-purple-300 bg-purple-500/10 px-1.5 py-0.5 rounded font-bold uppercase" title={m.vram_bytes ? `${formatBytes(m.vram_bytes)} VRAM` : undefined}>
                      Loaded{m.vram_bytes ? ` -- ${formatBytes(m.vram_bytes)}` : ""}
                    </span>
                  )}
                  {m.source.startsWith("Ollama") && (
                    <button
                      onClick={() => handleDeleteModel(m.name)}
                      disabled={busyModel === m.name}
                      className="text-slate-500 hover:text-red-400 transition disabled:opacity-50 cursor-pointer"
                      title={`Delete ${m.name}`}
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

interface ExtensionItem {
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
  kind: string;
  name: string;
  source: string;
  raw_text: string;
}

const EXTENSION_KINDS = ["plugin", "mcp", "skill", "openapi"] as const;

interface ExtensionsViewProps {
  kindFilter?: (typeof EXTENSION_KINDS)[number];
}

export function ExtensionsView({ kindFilter }: ExtensionsViewProps = {}): ReactElement {
  const [extensions, setExtensions] = useState<ExtensionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<PendingProposal | null>(null);

  const [kind, setKind] = useState<(typeof EXTENSION_KINDS)[number]>(kindFilter || "plugin");
  // Skills and MCP servers get their own tabs -- the plain Extensions page excludes both kinds so nothing duplicates.
  const visibleExtensions = kindFilter
    ? extensions.filter((e) => e.kind === kindFilter)
    : extensions.filter((e) => e.kind !== "skill" && e.kind !== "mcp");
  const installableKinds = kindFilter ? EXTENSION_KINDS : EXTENSION_KINDS.filter((k) => k !== "skill" && k !== "mcp");
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [rawText, setRawText] = useState("");

  const fetchExtensions = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/extensions");
      if (res.ok) {
        const json = await res.json();
        setExtensions(json.extensions || []);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data fetch on mount
    fetchExtensions();
  }, [fetchExtensions]);

  const handlePropose = async () => {
    setError("");
    try {
      const res = await fetch("/api/extensions/propose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, name, source, raw_text: rawText }),
      });
      const json = await res.json();
      if (json.status !== "ok") {
        setError(json.message || "Propose failed");
        return;
      }
      setPending({
        pending_id: json.pending_id,
        skill_card: json.skill_card,
        warnings: json.warnings || [],
        kind,
        name,
        source,
        raw_text: rawText,
      });
    } catch {
      setError("Propose request failed");
    }
  };

  const handleConfirm = async (approved: boolean) => {
    if (!pending) return;
    try {
      const res = await fetch("/api/extensions/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pending_id: pending.pending_id,
          approved,
          kind: pending.kind,
          source: pending.source,
          raw_text: pending.raw_text,
        }),
      });
      const json = await res.json();
      if (approved && json.status !== "ok") {
        setError(json.message || "Install failed");
      } else {
        setName("");
        setSource("");
        setRawText("");
      }
    } catch {
      setError("Confirm request failed");
    } finally {
      setPending(null);
      fetchExtensions();
    }
  };

  const handleEnable = async (extName: string) => {
    await fetch(`/api/extensions/${encodeURIComponent(extName)}/enable`, { method: "POST" });
    fetchExtensions();
  };
  const handleDisable = async (extName: string) => {
    await fetch(`/api/extensions/${encodeURIComponent(extName)}/disable`, { method: "POST" });
    fetchExtensions();
  };
  const handleUninstall = async (extName: string) => {
    await fetch(`/api/extensions/${encodeURIComponent(extName)}`, { method: "DELETE" });
    fetchExtensions();
  };

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
      <div className="border-b border-white/5 pb-3 flex justify-between items-end">
        <div>
          <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
            <Puzzle className="w-5 h-5 text-slate-400" />
            {kindFilter ? "Skills" : "Extensions"}
          </h2>
          <p className="text-xs text-slate-500 font-mono mt-1">
            {kindFilter
              ? "SKILL.md extensions -- reuses the Extensions system, filtered to kind=skill"
              : "MCP Servers, SKILL.md, OpenAPI Imports & Native Plugins"}
          </p>
        </div>

        <Button onClick={fetchExtensions} className="font-mono">
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-purple-400" : ""}`} />
          Refresh
        </Button>
      </div>

      <div className="rounded-xl border border-white/5 p-4 bg-zinc-900/20 space-y-3 font-mono text-xs">
        <span className="text-[10px] text-slate-500 uppercase tracking-wider block font-bold">Install {kindFilter || "Extension"}</span>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
          {kindFilter ? (
            <input
              disabled
              value={kindFilter}
              className="bg-zinc-950 border border-white/10 rounded-lg px-2 py-1.5 text-slate-500"
            />
          ) : (
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as (typeof EXTENSION_KINDS)[number])}
              className="bg-zinc-950 border border-white/10 rounded-lg px-2 py-1.5 text-slate-200"
            >
              {installableKinds.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          )}
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="name"
            className="bg-zinc-950 border border-white/10 rounded-lg px-2 py-1.5 text-slate-200 placeholder:text-slate-600"
          />
          <input
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder="source (plugin name / URL / command)"
            className="bg-zinc-950 border border-white/10 rounded-lg px-2 py-1.5 text-slate-200 placeholder:text-slate-600 md:col-span-2"
          />
        </div>
        <textarea
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
          placeholder="raw_text (SKILL.md content / OpenAPI spec / MCP server spec -- not needed for plugin kind)"
          rows={3}
          className="w-full bg-zinc-950 border border-white/10 rounded-lg px-2 py-1.5 text-slate-200 placeholder:text-slate-600"
        />
        {error && <p className="text-red-400">{error}</p>}
        <Button onClick={handlePropose} disabled={!name}>
          <Plus className="w-3.5 h-3.5" />
          Propose Install
        </Button>
      </div>

      {pending && (
        <div className="rounded-xl border border-amber-500/30 p-4 bg-amber-950/10 space-y-3 font-mono text-xs">
          <span className="text-[10px] text-amber-400 uppercase tracking-wider block font-bold">Approve Install</span>
          <pre className="whitespace-pre-wrap text-slate-300">{pending.skill_card}</pre>
          {pending.warnings.length > 0 && (
            <div className="space-y-1">
              {pending.warnings.map((w, i) => (
                <p key={i} className="text-amber-400 flex items-center gap-1.5">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" /> {w}
                </p>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <Button variant="success" onClick={() => handleConfirm(true)}>
              Approve
            </Button>
            <Button variant="danger" onClick={() => handleConfirm(false)}>
              Decline
            </Button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 font-mono text-xs">
        {loading && visibleExtensions.length === 0 ? (
          <p className="text-slate-500 italic py-4 md:col-span-2 animate-pulse">Loading {kindFilter || "extensions"}...</p>
        ) : visibleExtensions.length === 0 ? (
          <p className="text-slate-500 italic py-4 md:col-span-2">No {kindFilter || "extensions"} installed.</p>
        ) : (
          visibleExtensions.map((ext) => (
            <div key={ext.name} className="p-4 rounded-xl bg-zinc-900/40 border border-white/5 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-mono text-slate-500 uppercase font-bold">{ext.kind}</span>
                <span
                  className={`text-[10px] uppercase font-bold font-mono px-1.5 py-0.5 rounded border ${
                    ext.enabled
                      ? "text-emerald-400 bg-emerald-950/40 border-emerald-500/20"
                      : "text-slate-500 bg-zinc-950/60 border-white/10"
                  }`}
                >
                  {ext.enabled ? "enabled" : "disabled"}
                </span>
              </div>
              <p className="text-xs font-bold text-slate-200 truncate">{ext.name}</p>
              <p className="text-[10px] text-slate-400 truncate">{ext.source || "--"}</p>
              <p className="text-[10px] text-slate-500">
                {ext.tool_names.length} tool{ext.tool_names.length === 1 ? "" : "s"}: {ext.tool_names.join(", ") || "none"}
              </p>
              <div className="flex gap-2 pt-1">
                {ext.enabled ? (
                  <Button size="sm" onClick={() => handleDisable(ext.name)}>
                    <Power className="w-3 h-3" /> Disable
                  </Button>
                ) : (
                  <Button size="sm" onClick={() => handleEnable(ext.name)}>
                    <Power className="w-3 h-3" /> Enable
                  </Button>
                )}
                <Button size="sm" variant="danger" onClick={() => handleUninstall(ext.name)}>
                  <Trash2 className="w-3 h-3" /> Uninstall
                </Button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export function SkillsView(): ReactElement {
  return <ExtensionsView kindFilter="skill" />;
}

const AGENT_STATUS_STYLE: Record<AgentRun["status"], { icon: typeof Circle; className: string; label: string }> = {
  running: { icon: Circle, className: "text-cyan-400 fill-cyan-400/20 animate-pulse", label: "running" },
  done: { icon: CheckCircle2, className: "text-emerald-400 fill-black", label: "done" },
  timeout: { icon: Clock, className: "text-amber-400", label: "timed out" },
  cancelled: { icon: XCircle, className: "text-slate-500", label: "cancelled" },
};

function AgentRunCard({ run, onCancel }: { run: AgentRun; onCancel: (agentId: string) => void }): ReactElement {
  const { icon: StatusIcon, className, label } = AGENT_STATUS_STYLE[run.status];
  const durationMs = run.finishedAt ? run.finishedAt - run.spawnedAt : null;
  const durationLabel = durationMs === null ? null : durationMs < 1000 ? `${durationMs}ms` : `${(durationMs / 1000).toFixed(1)}s`;

  return (
    <div className="p-3 rounded-lg bg-zinc-900/40 border border-white/5 space-y-1.5 font-mono text-xs">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5">
          <StatusIcon className={`w-3.5 h-3.5 shrink-0 ${className}`} />
          <span className="uppercase text-[10px] font-bold text-slate-400">{label}</span>
        </span>
        {durationLabel && <span className="text-[10px] text-slate-600">{durationLabel}</span>}
      </div>
      <p className="text-slate-300 break-words">{run.task}</p>
      {run.lastTool && run.status === "running" && (
        <p className="text-[10px] text-slate-500">using: {run.lastTool}</p>
      )}
      {run.result && (
        <p className="text-[10px] text-slate-500 pl-2 border-l border-white/5 break-words">{run.result}</p>
      )}
      {run.status === "running" && (
        <Button size="sm" variant="danger" onClick={() => onCancel(run.agentId)}>
          <X className="w-3 h-3" /> Cancel
        </Button>
      )}
    </div>
  );
}

export function AgentsView(): ReactElement {
  const agentRuns = useCharlieStore((s) => s.agentRuns);

  const handleCancel = useCallback(async (agentId: string) => {
    try {
      await fetch(`/api/agents/${encodeURIComponent(agentId)}/cancel`, { method: "POST" });
    } catch {
      // ignore -- the agent_result WS event (or lack thereof) is the real signal
    }
  }, []);

  // Group by session so each turn's spawned agents render as a cluster
  // (a parent-child tree without a fixed pipeline shape -- roles are dynamic).
  const groups = useMemo(() => {
    const bySession = new Map<string, AgentRun[]>();
    for (const run of agentRuns) {
      const key = run.sessionId || "unknown";
      const list = bySession.get(key) || [];
      list.push(run);
      bySession.set(key, list);
    }
    return Array.from(bySession.entries());
  }, [agentRuns]);

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
      <div className="border-b border-white/5 pb-3">
        <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
          <GitBranch className="w-5 h-5 text-slate-400" />
          Agents
        </h2>
        <p className="text-xs text-slate-500 font-mono mt-1">
          Sub-agents delegated via spawn_agent, grouped by conversation turn
        </p>
      </div>

      {groups.length === 0 ? (
        <p className="text-slate-500 italic py-4 font-mono text-xs">
          No agents spawned yet -- most turns handle work directly without delegating.
        </p>
      ) : (
        <div className="space-y-5">
          {groups.map(([sessionId, runs]) => (
            <div key={sessionId} className="relative pl-4 border-l border-white/10 space-y-2">
              <span className="text-[10px] text-slate-500 uppercase tracking-wider font-bold font-mono">
                Session {sessionId === "unknown" ? "unknown" : sessionId.slice(0, 8)}
              </span>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {runs.map((run) => (
                  <AgentRunCard key={run.agentId} run={run} onCancel={handleCancel} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function MCPCenterView(): ReactElement {
  const [status, setStatus] = useState<{ enabled: boolean; connected: boolean } | null>(null);
  const [tools, setTools] = useState<McpToolLike[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [rStatus, rTools] = await Promise.all([fetch("/api/mcp/status"), fetch("/api/mcp/tools")]);
      if (rStatus.ok) setStatus(await rStatus.json());
      if (rTools.ok) setTools((await rTools.json()).tools || []);
    } catch {
      // ignore -- next poll retries
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data fetch on mount
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const servers = useMemo(() => groupMcpTools(tools), [tools]);

  return (
    <div className="flex-1 p-6 space-y-6 overflow-y-auto scrollbar animate-[rise_0.2s_ease-out]">
      <div className="border-b border-white/5 pb-3 flex justify-between items-end">
        <div>
          <h2 className="font-display text-xl font-bold uppercase tracking-wide flex items-center gap-2">
            <Cable className="w-5 h-5 text-slate-400" />
            MCP Servers
          </h2>
          <p className="text-xs text-slate-500 font-mono mt-1">
            Model Context Protocol servers configured in mcp_config.json
          </p>
        </div>
        <Button onClick={fetchData} className="font-mono">
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-purple-400" : ""}`} />
          Refresh
        </Button>
      </div>

      {status && !status.enabled && (
        <p className="text-slate-500 italic py-4 font-mono text-xs">
          MCP is disabled (MCP_ENABLED=false). No servers loaded.
        </p>
      )}

      {status?.enabled && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 font-mono text-xs">
          {loading && servers.length === 0 ? (
            <p className="text-slate-500 italic py-4 md:col-span-2 animate-pulse">Querying MCP client...</p>
          ) : servers.length === 0 ? (
            <p className="text-slate-500 italic py-4 md:col-span-2">
              No MCP tools discovered -- check mcp_config.json and server logs.
            </p>
          ) : (
            servers.map((server) => (
              <div key={server.name} className="p-4 rounded-xl bg-zinc-900/40 border border-white/5 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-slate-200 capitalize">{server.name}</span>
                  <span
                    className={`text-[10px] uppercase font-bold font-mono px-1.5 py-0.5 rounded border ${
                      status.connected
                        ? "text-emerald-400 bg-emerald-950/40 border-emerald-500/20"
                        : "text-slate-500 bg-zinc-950/60 border-white/10"
                    }`}
                  >
                    {status.connected ? "connected" : "disconnected"}
                  </span>
                </div>
                <p className="text-[10px] text-slate-500">
                  {server.count} tool{server.count === 1 ? "" : "s"}: {server.tools.join(", ")}
                </p>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
