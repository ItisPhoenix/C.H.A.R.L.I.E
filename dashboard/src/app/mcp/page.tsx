'use client'

import { useEffect, useState, useCallback } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { StatusDot } from '@/components/ui/StatusDot'
import { Toggle } from '@/components/ui/Toggle'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/layout/PageHeader'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { HudCorners } from '@/components/background/HudCorners'
import { fetchMCPServers, toggleMCPServer } from '@/lib/api'
import { truncate, cn } from '@/lib/utils'
import type { MCPServer, MCPTool } from '@/lib/types'

const statusDotMap: Record<string, 'online' | 'error' | 'idle'> = {
  running: 'online',
  error: 'error',
  stopped: 'idle',
}

function ToolRow({ tool }: { tool: MCPTool }) {
  return (
    <div className="flex items-center justify-between py-2 px-3 rounded-lg bg-charlie-dark/40 border border-charlie-border/30">
      <div className="flex-1 min-w-0">
        <span className="text-xs font-medium text-charlie-text font-mono">{tool.name}</span>
        {tool.description && (
          <p className="text-[11px] text-charlie-dim mt-0.5">{truncate(tool.description, 80)}</p>
        )}
      </div>
      <Badge variant="dim" className="ml-3 shrink-0">
        {tool.call_count} calls
      </Badge>
    </div>
  )
}

function ServerCard({ server, onToggle }: { server: MCPServer; onToggle: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <GlassCard className="!p-0 overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center justify-between p-4 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3">
          <StatusDot status={statusDotMap[server.status] || 'idle'} />
          <div>
            <span className="font-semibold text-sm text-charlie-text font-display tracking-wide">{server.name}</span>
            <div className="flex items-center gap-3 mt-0.5">
              <Badge variant="dim">{server.tools.length} tools</Badge>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Toggle
            enabled={server.enabled}
            onChange={() => onToggle(server.id)}
          />
          <svg
            className={cn(
              'w-4 h-4 text-charlie-dim transition-transform',
              expanded && 'rotate-180',
            )}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </div>

      {/* Expandable tool list */}
      {expanded && server.tools.length > 0 && (
        <div className="px-4 pb-4 space-y-2 border-t border-charlie-border/30 pt-3">
          <span className="text-xs text-charlie-dim font-medium">TOOLS</span>
          {server.tools.map((tool) => (
            <ToolRow key={tool.name} tool={tool} />
          ))}
        </div>
      )}

      {expanded && server.tools.length === 0 && (
        <div className="px-4 pb-4 border-t border-charlie-border/30 pt-3">
          <p className="text-xs text-charlie-dim text-center">No tools available</p>
        </div>
      )}
    </GlassCard>
  )
}

export default function MCPPage() {
  const [servers, setServers] = useState<MCPServer[]>([])
  const [loading, setLoading] = useState(true)

  const loadServers = useCallback(async () => {
    try {
      const data = await fetchMCPServers()
      setServers(data.servers)
    } catch {
      // keep existing state
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadServers()
  }, [loadServers])

  async function handleToggle(id: string) {
    try {
      await toggleMCPServer(id)
      await loadServers()
    } catch {
      // ignore
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <LoadingSpinner label="Loading MCP servers..." />
      </div>
    )
  }

  const totalTools = servers.reduce((sum, s) => sum + s.tools.length, 0)

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <PageHeader
        title="MCP Servers"
        subtitle={`${servers.length} servers, ${totalTools} tools`}
      />

      {servers.length === 0 ? (
        <EmptyState
          title="No MCP servers"
          description="No Model Context Protocol servers are configured"
        />
      ) : (
        <div className="space-y-3">
          {servers.map((server) => (
            <ServerCard key={server.id} server={server} onToggle={handleToggle} />
          ))}
        </div>
      )}
    </div>
  )
}
