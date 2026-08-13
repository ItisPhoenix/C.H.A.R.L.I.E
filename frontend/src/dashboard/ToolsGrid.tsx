import { useEffect, useState, type ReactElement } from "react";
import { Panel } from "./Panel";

interface ToolInfo {
  name: string;
  description: string;
  owner: string;
  risk_class: string | null;
}

function asTools(value: unknown): ToolInfo[] {
  if (!value || typeof value !== "object" || !Array.isArray((value as { tools?: unknown }).tools)) return [];
  return (value as { tools: unknown[] }).tools.flatMap((tool) => {
    if (!tool || typeof tool !== "object") return [];
    const item = tool as Record<string, unknown>;
    const name = typeof item.name === "string" ? item.name : "";
    if (!name) return [];
    return [{
      name,
      description: typeof item.description === "string" ? item.description : "",
      owner: typeof item.owner === "string" ? item.owner : "",
      risk_class: typeof item.risk_class === "string" ? item.risk_class : null,
    }];
  });
}

export function ToolsGrid(): ReactElement {
  const [tools, setTools] = useState<ToolInfo[]>([]);

  useEffect(() => {
    let active = true;
    fetch("/api/tools")
      .then((response) => response.ok ? response.json() : null)
      .then((data: unknown) => { if (active) setTools(asTools(data)); })
      .catch(() => { if (active) setTools([]); });
    return () => { active = false; };
  }, []);

  return (
    <Panel id="tools" title="Tools">
      {tools.length === 0 ? <p className="tool-empty">No tools reported.</p> : <div className="tool-grid">
        {tools.slice(0, 12).map((tool) => <article className="tool-entry" key={tool.name} title={tool.description}>
          <span aria-hidden="true">{tool.owner || "tool"}</span><small>{tool.name}</small>
        </article>)}
      </div>}
    </Panel>
  );
}
