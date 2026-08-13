import { useEffect, type ReactElement } from "react";
import { useCharlieStore } from "../store/charlie";
import { Panel } from "./Panel";

export function McpConnections(): ReactElement {
  const statuses = useCharlieStore((state) => state.mcpStatus);
  const seedMcpStatus = useCharlieStore((state) => state.seedMcpStatus);

  useEffect(() => {
    fetch("/api/mcp/status")
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("MCP request failed")))
      .then((data: { servers?: Record<string, boolean> }) => seedMcpStatus(data.servers ?? {}))
      .catch(() => undefined);
  }, [seedMcpStatus]);

  const names = Object.keys(statuses).slice(0, 6);
  const connected = Object.values(statuses).filter((value) => value.status === "connected").length;

  return (
    <Panel id="mcp" title="Connections">
      <div className="mcp-summary"><span>MCP Servers</span><strong>{names.length === 0 ? "Unavailable" : `${connected} Connected`}</strong></div>
      {names.length === 0 ? <p className="mcp-empty">No MCP servers reported.</p> : <div className="mcp-list">
        {names.map((name) => <div className="mcp-node" key={name} title={name}><span>{name}</span></div>)}
      </div>}
    </Panel>
  );
}
