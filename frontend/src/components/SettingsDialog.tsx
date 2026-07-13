"use client";

import { useEffect, useState } from "react";
import type { ReactElement } from "react";
import { useCharlieStore, rgba } from "@/store/useCharlieStore";

interface McpServerRow {
  name: string;
  command: string;
  args: string;
}

export default function SettingsDialog(): ReactElement | null {
  const settingsOpen = useCharlieStore((s) => s.settingsOpen);
  const setSettingsOpen = useCharlieStore((s) => s.setSettingsOpen);
  const accentColor = useCharlieStore((s) => s.accentColor);
  const connected = useCharlieStore((s) => s.connected);

  const [activeTab, setActiveTab] = useState<"general" | "mcp" | "plugins">("general");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [restarting, setRestarting] = useState(false);

  // Form states
  const [gpuDevice, setGpuDevice] = useState("cuda");
  const [kokoroLang, setKokoroLang] = useState("en-us");
  const [kokoroVoice, setKokoroVoice] = useState("af_heart");
  const [whisperModel, setWhisperModel] = useState("large-v3");
  const [wakeWordEnabled, setWakeWordEnabled] = useState(false);
  const [blackboardEnabled, setBlackboardEnabled] = useState(true);
  const [mcpEnabled, setMcpEnabled] = useState(false);
  const [pluginsEnabled, setPluginsEnabled] = useState(false);
  const [mcpServers, setMcpServers] = useState<McpServerRow[]>([]);
  const [pluginAllowDirs, setPluginAllowDirs] = useState<string[]>([]);
  const [newDir, setNewDir] = useState("");

  // Load config on open
  useEffect(() => {
    if (!settingsOpen) return;

    setLoading(true);
    fetch("/api/config")
      .then((r) => r.json())
      .then((data) => {
        setGpuDevice(data.GPU_DEVICE || "cuda");
        setKokoroLang(data.KOKORO_LANG || "en-us");
        setKokoroVoice(data.KOKORO_VOICE || "af_heart");
        setWhisperModel(data.WHISPER_MODEL || "large-v3");
        setWakeWordEnabled(Boolean(data.WAKE_WORD_ENABLED));
        setBlackboardEnabled(Boolean(data.BLACKBOARD_ENABLED));
        setMcpEnabled(Boolean(data.MCP_ENABLED));
        setPluginsEnabled(Boolean(data.PLUGINS_ENABLED));

        // Parse MCP servers
        const rawServers: string[] = data.MCP_SERVERS || [];
        const parsed = rawServers.map((s) => {
          const parts = s.split("|");
          return {
            name: parts[0] || "",
            command: parts[1] || "",
            args: parts.slice(2).join(","),
          };
        });
        setMcpServers(parsed);
        setPluginAllowDirs(data.PLUGIN_ALLOW_DIRS || []);
        setRestartNeeded(false);
      })
      .catch((e) => console.error("Error loading config", e))
      .finally(() => setLoading(false));
  }, [settingsOpen]);

  if (!settingsOpen) return null;

  const handleAddServer = () => {
    setMcpServers([...mcpServers, { name: "", command: "", args: "" }]);
  };

  const handleRemoveServer = (index: number) => {
    setMcpServers(mcpServers.filter((_, i) => i !== index));
  };

  const handleServerChange = (index: number, field: keyof McpServerRow, val: string) => {
    setMcpServers(
      mcpServers.map((s, i) => (i === index ? { ...s, [field]: val } : s))
    );
  };

  const handleAddDir = () => {
    if (newDir.trim() && !pluginAllowDirs.includes(newDir.trim())) {
      setPluginAllowDirs([...pluginAllowDirs, newDir.trim()]);
      setNewDir("");
    }
  };

  const handleRemoveDir = (index: number) => {
    setPluginAllowDirs(pluginAllowDirs.filter((_, i) => i !== index));
  };

  const handleSave = async () => {
    setSaving(true);
    // Serialize MCP Servers
    const serializedServers = mcpServers
      .filter((s) => s.name.trim() && s.command.trim())
      .map((s) => `${s.name.trim()}|${s.command.trim()}|${s.args.trim()}`);

    const payload = {
      GPU_DEVICE: gpuDevice,
      KOKORO_LANG: kokoroLang,
      KOKORO_VOICE: kokoroVoice,
      WHISPER_MODEL: whisperModel,
      WAKE_WORD_ENABLED: wakeWordEnabled,
      BLACKBOARD_ENABLED: blackboardEnabled,
      MCP_ENABLED: mcpEnabled,
      PLUGINS_ENABLED: pluginsEnabled,
      MCP_SERVERS: serializedServers,
      PLUGIN_ALLOW_DIRS: pluginAllowDirs,
    };

    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        setRestartNeeded(true);
      }
    } catch (e) {
      console.error("Failed to save configuration", e);
    } finally {
      setSaving(false);
    }
  };

  const handleRestartEngines = () => {
    setRestarting(true);
    // Find active websocket connection to send reload command
    const wsUrl = `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;
    const tempSocket = new WebSocket(wsUrl);
    tempSocket.onopen = () => {
      tempSocket.send(JSON.stringify({ type: "system_restart" }));
      setTimeout(() => {
        tempSocket.close();
        setRestarting(false);
        setRestartNeeded(false);
        setSettingsOpen(false);
      }, 1000);
    };
    tempSocket.onerror = () => {
      setRestarting(false);
    };
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-xl animate-fade-in">
      <div
        className="glass w-full max-w-2xl overflow-hidden flex flex-col max-h-[85vh]"
        style={{
          boxShadow: `0 8px 32px rgba(0,0,0,0.4), 0 0 40px 0 ${rgba(accentColor, 0.15)}`,
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-glass-border)] shrink-0">
          <div className="flex items-center gap-2">
            <svg viewBox="0 0 24 24" className="w-5 h-5" style={{ color: accentColor }} fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
            <h2 className="text-base font-semibold text-[var(--color-text-primary)]">System Settings</h2>
          </div>
          <button
            onClick={() => setSettingsOpen(false)}
            className="p-1 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-surface-hover)] cursor-pointer transition"
          >
            <svg viewBox="0 0 24 24" className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Navigation Tabs */}
        <div className="flex px-6 border-b border-[var(--color-glass-border)] shrink-0 bg-[var(--color-glass-bg-2)] gap-1.5">
          {(["general", "mcp", "plugins"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setActiveTab(t)}
              className="px-4 py-3 text-xs font-semibold uppercase tracking-wider relative cursor-pointer transition-colors"
              style={{
                color: activeTab === t ? "var(--color-text-primary)" : "var(--color-text-muted)",
              }}
            >
              {t === "general" ? "General Settings" : t === "mcp" ? "MCP Servers" : "Plugins"}
              {activeTab === t && (
                <div
                  className="absolute bottom-0 left-0 right-0 h-0.5 rounded-full"
                  style={{ background: accentColor }}
                />
              )}
            </button>
          ))}
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6 scrollbar">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-20 gap-3">
              <div className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: `${accentColor} transparent ${accentColor} transparent` }} />
              <p className="text-xs text-[var(--color-text-muted)] font-mono">Loading current configurations...</p>
            </div>
          ) : (
            <>
              {activeTab === "general" && (
                <div className="space-y-4">
                  {/* GPU device */}
                  <div className="flex flex-col gap-1.5">
                    <label className="text-xs text-[var(--color-text-muted)] font-semibold font-mono">GPU Device</label>
                    <select
                      value={gpuDevice}
                      onChange={(e) => setGpuDevice(e.target.value)}
                      className="w-full bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-glass-border-hover)]"
                    >
                      <option value="cuda">CUDA GPU Acceleration</option>
                      <option value="cpu">CPU Only</option>
                    </select>
                  </div>

                  {/* Whisper model */}
                  <div className="flex flex-col gap-1.5">
                    <label className="text-xs text-[var(--color-text-muted)] font-semibold font-mono">Whisper ASR Model</label>
                    <select
                      value={whisperModel}
                      onChange={(e) => setWhisperModel(e.target.value)}
                      className="w-full bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-glass-border-hover)]"
                    >
                      <option value="large-v3">large-v3 (Default - Best Accuracy)</option>
                      <option value="distil-large-v3">distil-large-v3 (Faster - Recommended for Low Memory)</option>
                      <option value="medium">medium</option>
                      <option value="small">small</option>
                      <option value="base">base</option>
                    </select>
                  </div>

                  {/* Kokoro voice */}
                  <div className="flex flex-col gap-1.5">
                    <label className="text-xs text-[var(--color-text-muted)] font-semibold font-mono">Kokoro TTS Voice</label>
                    <select
                      value={kokoroVoice}
                      onChange={(e) => setKokoroVoice(e.target.value)}
                      className="w-full bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-glass-border-hover)]"
                    >
                      <option value="af_heart">af_heart (Default Warm Female)</option>
                      <option value="af_bella">af_bella</option>
                      <option value="af_nicole">af_nicole</option>
                      <option value="am_adam">am_adam (Warm Male)</option>
                      <option value="am_michael">am_michael</option>
                    </select>
                  </div>

                  {/* Kokoro lang */}
                  <div className="flex flex-col gap-1.5">
                    <label className="text-xs text-[var(--color-text-muted)] font-semibold font-mono">TTS Language Code</label>
                    <input
                      type="text"
                      value={kokoroLang}
                      onChange={(e) => setKokoroLang(e.target.value)}
                      className="w-full bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-glass-border-hover)]"
                      placeholder="e.g. en-us"
                    />
                  </div>

                  {/* Toggles */}
                  <div className="grid grid-cols-2 gap-4 pt-2">
                    <label className="flex items-center gap-3 p-3 bg-[var(--color-surface-hover)] hover:bg-[var(--color-glass-bg-2)] rounded-xl cursor-pointer border border-[var(--color-glass-border)] transition">
                      <input
                        type="checkbox"
                        checked={wakeWordEnabled}
                        onChange={(e) => setWakeWordEnabled(e.target.checked)}
                        className="w-4 h-4 rounded"
                        style={{ accentColor }}
                      />
                      <div className="flex flex-col">
                        <span className="text-xs font-semibold text-[var(--color-text-primary)]">Hands-free Wake Word</span>
                        <span className="text-[10px] text-[var(--color-text-muted)] font-mono">Listen for "Hey Charlie"</span>
                      </div>
                    </label>

                    <label className="flex items-center gap-3 p-3 bg-[var(--color-surface-hover)] hover:bg-[var(--color-glass-bg-2)] rounded-xl cursor-pointer border border-[var(--color-glass-border)] transition">
                      <input
                        type="checkbox"
                        checked={blackboardEnabled}
                        onChange={(e) => setBlackboardEnabled(e.target.checked)}
                        className="w-4 h-4 rounded"
                        style={{ accentColor }}
                      />
                      <div className="flex flex-col">
                        <span className="text-xs font-semibold text-[var(--color-text-primary)]">Blackboard Orchestration</span>
                        <span className="text-[10px] text-[var(--color-text-muted)] font-mono">Enable task dashboard & goals</span>
                      </div>
                    </label>
                  </div>
                </div>
              )}

              {activeTab === "mcp" && (
                <div className="space-y-4">
                  <div className="flex items-center justify-between border-b border-[var(--color-glass-border)] pb-2">
                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        id="mcp-enabled"
                        checked={mcpEnabled}
                        onChange={(e) => setMcpEnabled(e.target.checked)}
                        className="w-4 h-4 rounded"
                        style={{ accentColor }}
                      />
                      <label htmlFor="mcp-enabled" className="text-xs font-semibold text-[var(--color-text-primary)] cursor-pointer select-none">
                        Enable Model Context Protocol (MCP) Subsystem
                      </label>
                    </div>
                    {mcpEnabled && (
                      <button
                        onClick={handleAddServer}
                        className="px-2.5 py-1 text-[10px] font-semibold tracking-wider uppercase bg-[var(--color-surface-hover)] hover:bg-[var(--color-glass-bg-2)] text-[var(--color-text-primary)] rounded-md cursor-pointer transition flex items-center gap-1"
                      >
                        <span className="text-xs font-normal">+</span> Add Server
                      </button>
                    )}
                  </div>

                  {mcpEnabled ? (
                    <div className="space-y-3">
                      {mcpServers.length === 0 ? (
                        <p className="text-xs text-[var(--color-text-muted)] font-mono py-6 text-center">
                          No custom MCP servers configured. Click 'Add Server' above.
                        </p>
                      ) : (
                        mcpServers.map((s, idx) => (
                          <div
                            key={idx}
                            className="p-3 bg-[var(--color-surface-hover)] rounded-xl border border-[var(--color-glass-border)] flex flex-col gap-3 relative"
                          >
                            <button
                              onClick={() => handleRemoveServer(idx)}
                              className="absolute top-2 right-2 text-[var(--color-text-muted)] hover:text-status-error p-1 rounded hover:bg-[var(--color-glass-bg-2)] cursor-pointer transition"
                              title="Delete Server"
                            >
                              <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M18 6 6 18M6 6l12 12" />
                              </svg>
                            </button>

                            <div className="grid grid-cols-3 gap-3 pr-6">
                              <div className="flex flex-col gap-1">
                                <label className="text-[10px] font-semibold text-[var(--color-text-muted)] font-mono uppercase">Server Name</label>
                                <input
                                  type="text"
                                  value={s.name}
                                  onChange={(e) => handleServerChange(idx, "name", e.target.value)}
                                  className="bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--color-text-primary)] focus:outline-none"
                                  placeholder="e.g. filesystem"
                                />
                              </div>

                              <div className="flex flex-col gap-1">
                                <label className="text-[10px] font-semibold text-[var(--color-text-muted)] font-mono uppercase">Executable / Cmd</label>
                                <input
                                  type="text"
                                  value={s.command}
                                  onChange={(e) => handleServerChange(idx, "command", e.target.value)}
                                  className="bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--color-text-primary)] focus:outline-none"
                                  placeholder="e.g. npx"
                                />
                              </div>

                              <div className="flex flex-col gap-1">
                                <label className="text-[10px] font-semibold text-[var(--color-text-muted)] font-mono uppercase">Arguments</label>
                                <input
                                  type="text"
                                  value={s.args}
                                  onChange={(e) => handleServerChange(idx, "args", e.target.value)}
                                  className="bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--color-text-primary)] focus:outline-none"
                                  placeholder="e.g. -y, @modelcontextprotocol/server-filesystem"
                                />
                              </div>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  ) : (
                    <div className="py-8 text-center bg-[var(--color-glass-bg-2)] rounded-xl border border-dashed border-[var(--color-glass-border)]">
                      <p className="text-xs text-[var(--color-text-muted)] font-mono">
                        MCP integration is currently disabled. Toggle the checkbox to configure servers.
                      </p>
                    </div>
                  )}
                </div>
              )}

              {activeTab === "plugins" && (
                <div className="space-y-4">
                  <div className="flex items-center gap-2 border-b border-[var(--color-glass-border)] pb-3">
                    <input
                      type="checkbox"
                      id="plugins-enabled"
                      checked={pluginsEnabled}
                      onChange={(e) => setPluginsEnabled(e.target.checked)}
                      className="w-4 h-4 rounded"
                      style={{ accentColor }}
                    />
                    <label htmlFor="plugins-enabled" className="text-xs font-semibold text-[var(--color-text-primary)] cursor-pointer select-none">
                      Enable Hybrid Plugins System
                    </label>
                  </div>

                  {pluginsEnabled ? (
                    <div className="space-y-4">
                      <div className="flex flex-col gap-1.5">
                        <label className="text-xs text-[var(--color-text-muted)] font-semibold font-mono">Allowed Filesystem Access Directories</label>
                        <div className="flex gap-2">
                          <input
                            type="text"
                            value={newDir}
                            onChange={(e) => setNewDir(e.target.value)}
                            onKeyDown={(e) => e.key === "Enter" && handleAddDir()}
                            className="flex-1 bg-[var(--color-glass-bg-2)] border border-[var(--color-glass-border)] rounded-lg px-3 py-2 text-xs text-[var(--color-text-primary)] focus:outline-none"
                            placeholder="e.g. C:\Users\User\Documents"
                          />
                          <button
                            onClick={handleAddDir}
                            className="px-3.5 bg-[var(--color-surface-hover)] hover:bg-[var(--color-glass-bg-2)] text-[var(--color-text-primary)] rounded-lg text-xs font-semibold cursor-pointer transition"
                          >
                            Add
                          </button>
                        </div>
                      </div>

                      <div className="space-y-1.5">
                        {pluginAllowDirs.length === 0 ? (
                          <p className="text-xs text-[var(--color-text-muted)] font-mono">
                            No directory restrictions set. Plugins will fall back to workspace root permissions.
                          </p>
                        ) : (
                          pluginAllowDirs.map((dir, idx) => (
                            <div
                              key={idx}
                              className="flex items-center justify-between p-2 bg-[var(--color-surface-hover)] border border-[var(--color-glass-border)] rounded-lg px-3"
                            >
                              <span className="text-xs text-[var(--color-text-secondary)] font-mono truncate mr-3">{dir}</span>
                              <button
                                onClick={() => handleRemoveDir(idx)}
                                className="text-[var(--color-text-muted)] hover:text-status-error p-1 cursor-pointer transition"
                              >
                                <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2">
                                  <path d="M18 6 6 18M6 6l12 12" />
                                </svg>
                              </button>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="py-8 text-center bg-[var(--color-glass-bg-2)] rounded-xl border border-dashed border-[var(--color-glass-border)]">
                      <p className="text-xs text-[var(--color-text-muted)] font-mono">
                        Hybrid plugin execution is disabled. Toggle the checkbox to configure directory access.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer Warning & Actions */}
        <div className="px-6 py-4 bg-[var(--color-glass-bg-2)] border-t border-[var(--color-glass-border)] shrink-0 flex items-center justify-between gap-4">
          <div className="flex-1">
            {restartNeeded && (
              <div className="flex items-center gap-2 text-[10px] text-status-warning font-semibold uppercase tracking-wider animate-pulse">
                <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
                </svg>
                <span>Save succeeded. Restart engines to apply.</span>
              </div>
            )}
          </div>

          <div className="flex items-center gap-3">
            {restartNeeded ? (
              <button
                onClick={handleRestartEngines}
                disabled={restarting || !connected}
                className="px-4 py-2 text-xs font-semibold rounded-lg bg-status-warning hover:brightness-110 disabled:opacity-40 text-black cursor-pointer disabled:cursor-not-allowed transition flex items-center gap-1.5"
              >
                {restarting ? (
                  <>
                    <div className="w-3 h-3 rounded-full border border-t-transparent animate-spin border-black" />
                    <span>Restarting...</span>
                  </>
                ) : (
                  <>
                    <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2.5">
                      <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
                    </svg>
                    <span>Restart Engines</span>
                  </>
                )}
              </button>
            ) : (
              <button
                onClick={handleSave}
                disabled={saving || loading}
                className="px-4 py-2 text-xs font-semibold rounded-lg text-white hover:brightness-110 disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed transition"
                style={{ background: accentColor }}
              >
                {saving ? "Saving..." : "Save Changes"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
