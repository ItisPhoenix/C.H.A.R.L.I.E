import { useEffect, useMemo, useState, type ReactElement } from "react";
import { Panel } from "./Panel";

interface ConfigField {
  key: string;
  label: string;
  group: string;
  type: "bool" | "int" | "float" | "list" | "str";
  secret: boolean;
  restart: string | null;
  value: unknown;
  is_set: boolean | null;
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

export function Settings(): ReactElement {
  const [fields, setFields] = useState<ConfigField[]>([]);
  const [drafts, setDrafts] = useState<Record<string, unknown>>({});
  const [status, setStatus] = useState("Loading settings...");
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [capabilities, setCapabilities] = useState<CapabilitySnapshot>({});
  const [modelSnapshot, setModelSnapshot] = useState<ModelSnapshot>({});
  const [modelsLoading, setModelsLoading] = useState(true);

  useEffect(() => {
    void fetch("/api/config")
      .then(async (response) => response.ok ? response.json() as Promise<{ fields?: ConfigField[] }> : Promise.reject(new Error("Settings unavailable")))
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
      setModelSnapshot(await response.json() as ModelSnapshot);
    } catch {
      setModelSnapshot({});
    } finally {
      setModelsLoading(false);
    }
  }

  useEffect(() => {
    void fetch("/api/capabilities")
      .then(async (response) => response.ok ? response.json() as Promise<CapabilitySnapshot> : Promise.reject(new Error("Capabilities unavailable")))
      .then(setCapabilities)
      .catch(() => setCapabilities({}));
  }, []);

  useEffect(() => { void refreshModels(); }, []);

  useEffect(() => {
    void fetch("/api/audit")
      .then(async (response) => response.ok ? response.json() as Promise<{ entries?: AuditEntry[] }> : Promise.reject(new Error("Audit unavailable")))
      .then((data) => setAudit(Array.isArray(data.entries) ? data.entries : []))
      .catch(() => setAudit([]));
  }, []);

  const groups = useMemo(() => fields.reduce<Record<string, ConfigField[]>>((result, field) => {
    (result[field.group] ??= []).push(field);
    return result;
  }, {}), [fields]);
  const availableModels = useMemo(() => Array.from(new Set([String(fields.find((field) => field.key === "LLM_MODEL")?.value ?? ""), ...(modelSnapshot.models ?? [])])).filter(Boolean), [fields, modelSnapshot.models]);

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

  return (
    <Panel id="settings" title="Settings">
      <div className="settings-workspace">
        <div className="settings-intro">
          <div><h3>Runtime controls</h3><p>Changes are saved locally first, then applied when you choose Reload runtime.</p></div>
          <button type="button" className="settings-refresh" onClick={() => void refreshModels()} disabled={modelsLoading}>{modelsLoading ? "Discovering..." : "Refresh models"}</button>
        </div>
        <div className="settings-groups">{Object.entries(groups).map(([group, groupFields]) => (
          <section className="settings-group" key={group}>
            <h3>{group}</h3>
            {groupFields.map((field) => (
              <label className="settings-field" key={field.key}>
                <span>{field.label}{field.restart ? <small>Restart: {field.restart}</small> : null}</span>
                {field.secret ? (
                  <input aria-label={field.label} type="password" placeholder={field.is_set ? "Configured" : "Not configured"} onChange={(event) => setDrafts((current) => ({ ...current, [field.key]: event.target.value }))} />
                ) : field.type === "bool" ? (
                  <input aria-label={field.label} type="checkbox" checked={Boolean(fieldValue(field, drafts))} onChange={(event) => setDrafts((current) => ({ ...current, [field.key]: event.target.checked }))} />
                ) : field.key === "LLM_MODEL" && (modelSnapshot.models?.length ?? 0) > 0 ? (
                  <>
                    <input aria-label={field.label} list="charlie-model-options" value={String(fieldValue(field, drafts) ?? "")} onChange={(event) => setDrafts((current) => ({ ...current, [field.key]: event.target.value }))} />
                    <datalist id="charlie-model-options">
                      {availableModels.map((model) => <option key={model} value={model} />)}
                    </datalist>
                  </>
                ) : (
                  <input aria-label={field.label} type={field.type === "int" || field.type === "float" ? "number" : "text"} value={String(fieldValue(field, drafts) ?? "")} onChange={(event) => setDrafts((current) => ({ ...current, [field.key]: event.target.value }))} />
                )}
              </label>
            ))}
          </section>
        ))}</div>
        <section className="settings-group settings-audit"><h3>Audit</h3>{audit.length === 0 ? <p className="settings-status">No audit entries available.</p> : audit.map((entry) => <p key={entry.id}><time>{new Date(entry.created_at).toLocaleString()}</time><strong>{entry.tool_name}</strong><span>{entry.outcome}</span></p>)}</section>
        <section className="settings-group settings-capabilities">
          <h3>Live capability status</h3>
          <p>{capabilities.tools?.length ?? 0} registered tools</p>
          {Object.entries(capabilities.runtime ?? {}).map(([name, health]) => <p key={name}><strong>{name}</strong><span>{health.detail ?? health.status ?? "Unknown"}</span></p>)}
        </section>
        {status ? <p className="settings-status">{status}</p> : null}
        <footer className="settings-actions"><button type="button" onClick={() => void save()} disabled={Object.keys(drafts).length === 0}>Save</button><button type="button" onClick={() => void reload()}>Reload runtime</button><button type="button" onClick={() => void exportAudit()}>Export audit</button></footer>
      </div>
    </Panel>
  );
}
