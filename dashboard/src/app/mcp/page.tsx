'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { StatusDot } from '@/components/ui/StatusDot'
import { Toggle } from '@/components/ui/Toggle'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/layout/PageHeader'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorState } from '@/components/ui/ErrorState'
import { Modal } from '@/components/ui/Modal'
import * as api from '@/lib/api'
import { cn } from '@/lib/utils'
import { ChevronDown, Plus, Trash2, Play, Plug, PlugZap } from 'lucide-react'

interface MCPTool {
  name: string
  description?: string
  input_schema?: Record<string, unknown>
  call_count?: number
}

interface MCPServer {
  id: string
  name: string
  status: string
  enabled: boolean
  tools: MCPTool[]
  config?: Record<string, unknown>
}

const statusMap: Record<string, 'online' | 'error' | 'idle'> = {
  connected: 'online',
  configured: 'idle',
  disconnected: 'idle',
  disabled: 'idle',
  error: 'error',
}

function DockerGatewayBanner({
  gateway,
  busy,
  onAction,
}: {
  gateway: { reachable: boolean; managed_here: boolean; container_id?: string }
  busy: boolean
  onAction: (action: 'start' | 'stop') => void
}) {
  // Only show the banner when the docker-mcp-gateway server is configured
  // in charlie_config.json. We detect it from the doc-string context: the
  // page already knows about configured servers. Here we just render based
  // on probe state — the banner is informational and small.
  if (gateway.reachable) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-charlie-border bg-charlie-darker/40 text-xs">
        <span className="w-2 h-2 rounded-full bg-emerald-400" />
        <span className="text-charlie-text">Docker MCP Gateway reachable on :8080</span>
        {gateway.managed_here && (
          <Button
            variant="ghost"
            size="xs"
            disabled={busy}
            onClick={() => onAction('stop')}
            className="ml-auto"
          >
            {busy ? 'Stopping...' : 'Stop Gateway'}
          </Button>
        )}
      </div>
    )
  }
  return (
    <div className="flex items-center gap-3 px-3 py-2 rounded-lg border border-amber-500/30 bg-amber-500/5 text-xs">
      <span className="w-2 h-2 rounded-full bg-amber-400" />
      <span className="text-charlie-text">
        Docker MCP Gateway not reachable on localhost:8080
      </span>
      <Button
        variant="primary"
        size="xs"
        disabled={busy}
        onClick={() => onAction('start')}
        className="ml-auto"
      >
        {busy ? 'Starting...' : 'Start Gateway'}
      </Button>
    </div>
  )
}

function ToolTester({ serverId, tool, onClose }: { serverId: string; tool: MCPTool; onClose: () => void }) {
  const [args, setArgs] = useState('{}')
  const [result, setResult] = useState<string | null>(null)
  const [calling, setCalling] = useState(false)

  const handleCall = async () => {
    setCalling(true)
    setResult(null)
    try {
      const parsed = JSON.parse(args)
      const res = await api.callMCPTool(serverId, tool.name, parsed)
      setResult(res.ok ? (res.result || 'Success') : (res.error || 'Failed'))
    } catch (e) {
      setResult(`Error: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setCalling(false)
    }
  }

  return (
    <Modal open onClose={onClose} title={`Test: ${tool.name}`}>
      <div className="space-y-4">
        {tool.description && (
          <p className="text-sm text-charlie-dim">{tool.description}</p>
        )}
        {tool.input_schema && Object.keys(tool.input_schema).length > 0 && (
          <div>
            <label className="text-xs text-charlie-dim mb-1 block">Input Schema</label>
            <pre className="text-xs text-charlie-dim bg-charlie-dark p-2 rounded-lg overflow-auto max-h-32">
              {JSON.stringify(tool.input_schema, null, 2)}
            </pre>
          </div>
        )}
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Arguments (JSON)</label>
          <textarea
            value={args}
            onChange={(e) => setArgs(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-3 text-sm text-charlie-text font-mono focus:border-charlie-cyan focus:outline-none min-h-[80px]"
            placeholder='{"key": "value"}'
          />
        </div>
        <div className="flex gap-2">
          <Button onClick={handleCall} loading={calling} size="sm">
            <Play size={14} className="mr-1" /> Call Tool
          </Button>
          <Button variant="ghost" onClick={onClose} size="sm">Close</Button>
        </div>
        {result && (
          <div>
            <label className="text-xs text-charlie-dim mb-1 block">Result</label>
            <pre className="text-xs bg-charlie-dark border border-charlie-border p-3 rounded-lg overflow-auto max-h-64 text-charlie-text whitespace-pre-wrap">
              {result}
            </pre>
          </div>
        )}
      </div>
    </Modal>
  )
}

function AddServerModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [name, setName] = useState('')
  const [serverType, setServerType] = useState<'sse' | 'stdio'>('sse')
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [command, setCommand] = useState('')
  const [argsStr, setArgsStr] = useState('')
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    setSaving(true)
    try {
      const config: Record<string, unknown> = {}
      if (serverType === 'sse') {
        config.url = url
        if (token) config.token = token
      } else {
        config.command = command
        if (argsStr) config.args = argsStr.split(/\s+/)
      }
      const res = await api.addMCPServer(name, config)
      if (res.ok) {
        onAdded()
        onClose()
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open onClose={onClose} title="Add MCP Server">
      <div className="space-y-4">
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Server Name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
            placeholder="my-mcp-server"
          />
        </div>
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Type</label>
          <div className="flex gap-2">
            <Button variant={serverType === 'sse' ? 'primary' : 'ghost'} size="xs" onClick={() => setServerType('sse')}>SSE (Remote)</Button>
            <Button variant={serverType === 'stdio' ? 'primary' : 'ghost'} size="xs" onClick={() => setServerType('stdio')}>Stdio (Local)</Button>
          </div>
        </div>
        {serverType === 'sse' ? (
          <>
            <div>
              <label className="text-xs text-charlie-dim mb-1 block">URL</label>
              <input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
                placeholder="http://localhost:8080"
              />
            </div>
            <div>
              <label className="text-xs text-charlie-dim mb-1 block">Token (optional)</label>
              <input
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
                placeholder="Bearer token"
                type="password"
              />
            </div>
          </>
        ) : (
          <>
            <div>
              <label className="text-xs text-charlie-dim mb-1 block">Command</label>
              <input
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
                placeholder="npx"
              />
            </div>
            <div>
              <label className="text-xs text-charlie-dim mb-1 block">Arguments (space-separated)</label>
              <input
                value={argsStr}
                onChange={(e) => setArgsStr(e.target.value)}
                className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
                placeholder="-y @modelcontextprotocol/server-filesystem"
              />
            </div>
          </>
        )}
        <div className="flex gap-2 justify-end">
          <Button variant="ghost" onClick={onClose} size="sm">Cancel</Button>
          <Button onClick={handleSave} loading={saving} disabled={!name} size="sm">
            <Plus size={14} className="mr-1" /> Add Server
          </Button>
        </div>
      </div>
    </Modal>
  )
}

function ServerCard({ server, onRefresh }: { server: MCPServer; onRefresh: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const [testingTool, setTestingTool] = useState<MCPTool | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const isConnected = server.status === 'connected'

  const handleConnect = async () => {
    setConnecting(true)
    try {
      if (isConnected) {
        await api.disconnectMCPServer(server.id)
      } else {
        await api.connectMCPServer(server.id)
      }
      onRefresh()
    } finally {
      setConnecting(false)
    }
  }

  const handleToggle = async () => {
    await api.toggleMCPServer(server.id)
    onRefresh()
  }

  const handleDelete = async () => {
    if (!confirm(`Delete ${server.name}?`)) return
    setDeleting(true)
    try {
      await api.deleteMCPServer(server.id)
      onRefresh()
    } finally {
      setDeleting(false)
    }
  }

  return (
    <GlassCard className="!p-0 overflow-hidden">
      <div
        className="flex items-center justify-between p-4 cursor-pointer hover:bg-charlie-text/5 rounded-lg transition-colors"
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setExpanded(!expanded)
          }
        }}
      >
        <div className="flex items-center gap-3">
          <StatusDot status={statusMap[server.status] || 'idle'} />
          <div>
            <span className="font-semibold text-sm text-charlie-text font-display tracking-wide">{server.name}</span>
            <div className="flex items-center gap-2 mt-0.5">
              <Badge variant={isConnected ? 'green' : 'dim'}>{isConnected ? 'Connected' : server.status}</Badge>
              <Badge variant="dim">{server.tools.length} tools</Badge>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
          <Button
            variant={isConnected ? 'ghost' : 'primary'}
            size="sm"
            onClick={handleConnect}
            loading={connecting}
          >
            {isConnected ? <Plug size={14} className="mr-1" /> : <PlugZap size={14} className="mr-1" />}
            {isConnected ? 'Disconnect' : 'Connect'}
          </Button>
          <Toggle enabled={server.enabled} onChange={handleToggle} />
          <Button
            variant="danger"
            size="sm"
            onClick={handleDelete}
            loading={deleting}
            title="Delete server"
            className="!p-1.5"
          >
            <Trash2 size={14} />
          </Button>
          <ChevronDown
            size={16}
            className={cn('text-charlie-dim transition-transform', expanded && 'rotate-180')}
          />
        </div>
      </div>

      {expanded && (
        <div className="px-4 pb-4 border-t border-charlie-border/30 pt-3">
          {server.tools.length > 0 ? (
            <div className="space-y-2">
              <span className="text-xs text-charlie-dim font-medium">TOOLS ({server.tools.length})</span>
              {server.tools.map((tool) => (
                <div key={tool.name} className="flex items-center justify-between py-2 px-3 rounded-lg bg-charlie-dark/40 border border-charlie-border/30">
                  <div className="flex-1 min-w-0">
                    <span className="text-xs font-medium text-charlie-text font-mono">{tool.name}</span>
                    {tool.description && (
                      <p className="text-xs text-charlie-dim mt-0.5 truncate">{tool.description}</p>
                    )}
                  </div>
                  <Button variant="ghost" size="sm" onClick={() => setTestingTool(tool)}>
                    <Play size={12} className="mr-1" /> Test
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-charlie-dim text-center py-2">
              {isConnected ? 'No tools discovered' : 'Connect to discover tools'}
            </p>
          )}
        </div>
      )}

      {testingTool && (
        <ToolTester
          serverId={server.id}
          tool={testingTool}
          onClose={() => setTestingTool(null)}
        />
      )}
    </GlassCard>
  )
}

export default function MCPPage() {
  const [servers, setServers] = useState<MCPServer[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [autoConnecting, setAutoConnecting] = useState(false)
  const [gateway, setGateway] = useState<{
    reachable: boolean
    managed_here: boolean
    container_id?: string
  }>({ reachable: false, managed_here: false })
  const [gatewayBusy, setGatewayBusy] = useState(false)
  const autoConnectDone = useRef(false)

  const loadServers = useCallback(async () => {
    try {
      setError(null)
      const data = await api.fetchMCPServers()
      setServers(data.servers)
      return data.servers
    } catch (e) {
      console.error('Failed to load MCP servers:', e)
      setError('Failed to load MCP servers')
      return []
    } finally {
      setLoading(false)
    }
  }, [])

  const refreshGateway = useCallback(async () => {
    try {
      const s = await api.fetchDockerGatewayStatus()
      setGateway({
        reachable: s.reachable,
        managed_here: s.managed_here,
        container_id: s.container_id,
      })
    } catch {
      // Backend unavailable — leave as-is
    }
  }, [])

  const handleGatewayAction = useCallback(async (action: 'start' | 'stop') => {
    setGatewayBusy(true)
    try {
      if (action === 'start') {
        await api.startDockerGateway()
      } else {
        await api.stopDockerGateway()
      }
      await refreshGateway()
      await loadServers()
    } finally {
      setGatewayBusy(false)
    }
  }, [refreshGateway, loadServers])

  // Auto-connect to enabled servers that are not yet connected.
  // Runs once after the initial fetch completes.
  const autoConnect = useCallback(async (serverList: MCPServer[]) => {
    const toConnect = serverList.filter(
      (s) => s.enabled && s.status !== 'connected' && s.status !== 'disabled'
    )
    if (toConnect.length === 0) return

    setAutoConnecting(true)
    try {
      // Connect sequentially so the UI updates incrementally
      for (const s of toConnect) {
        try {
          await api.connectMCPServer(s.id)
        } catch (e) {
          console.error(`Auto-connect failed for ${s.id}:`, e)
        }
      }
    } finally {
      setAutoConnecting(false)
      // Refresh to pick up connected state + discovered tools
      await loadServers()
    }
  }, [loadServers])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const serverList = await loadServers()
      if (!cancelled && !autoConnectDone.current) {
        autoConnectDone.current = true
        autoConnect(serverList)
      }
    })()
    refreshGateway()
    const probe = setInterval(refreshGateway, 10000)
    return () => { cancelled = true; clearInterval(probe) }
  }, [loadServers, autoConnect, refreshGateway])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <LoadingSpinner label="Loading MCP servers..." />
      </div>
    )
  }

  if (error) {
    return <ErrorState error={error} onRetry={loadServers} />
  }

  const totalTools = servers.reduce((sum, s) => sum + s.tools.length, 0)
  const connected = servers.filter((s) => s.status === 'connected').length

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <PageHeader
        title="MCP Servers"
        subtitle={`${servers.length} servers, ${connected} connected, ${totalTools} tools`}
        actions={
          <div className="flex items-center gap-2">
            {autoConnecting && (
              <span className="text-xs text-charlie-dim animate-pulse">Connecting...</span>
            )}
            <Button size="sm" onClick={() => setShowAdd(true)}>
              <Plus size={14} className="mr-1" /> Add Server
            </Button>
          </div>
        }
      />

      <DockerGatewayBanner
        gateway={gateway}
        busy={gatewayBusy}
        onAction={handleGatewayAction}
      />

      {servers.length === 0 ? (
        <EmptyState
          title="No MCP servers"
          description="Add an MCP server to get started with tool integrations"
          action={{ label: 'Add Server', onClick: () => setShowAdd(true) }}
        />
      ) : (
        <div className="space-y-3">
          {servers.map((server) => (
            <ServerCard key={server.id} server={server} onRefresh={loadServers} />
          ))}
        </div>
      )}

      {showAdd && (
        <AddServerModal onClose={() => setShowAdd(false)} onAdded={loadServers} />
      )}
    </div>
  )
}
