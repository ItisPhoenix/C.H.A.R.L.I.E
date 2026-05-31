'use client'

import { useEffect, useState, useCallback, useMemo } from 'react'
import { ReactFlow, Background, Controls } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { StatusDot } from '@/components/ui/StatusDot'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/layout/PageHeader'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { AgentNode } from '@/components/graphs/AgentNode'
import { useAutoLayout } from '@/components/graphs/useAutoLayout'
import * as api from '@/lib/api'
import { cn } from '@/lib/utils'
import type { AgentStatus, AgentInfo } from '@/lib/types'
import type { Node, Edge } from '@xyflow/react'

const nodeTypes = { agent: AgentNode }

const orchestratorVariant: Record<string, 'idle' | 'online' | 'warning' | 'error'> = {
  idle: 'idle',
  planning: 'warning',
  executing: 'online',
  error: 'error',
}

const agentStatusVariant: Record<string, 'cyan' | 'green' | 'red' | 'dim' | 'amber'> = {
  busy: 'cyan',
  idle: 'dim',
  error: 'red',
  waiting: 'amber',
}

function OrchestratorCard({ status }: { status: AgentStatus['orchestrator'] }) {
  const isExecuting = status.status === 'executing'

  return (
    <GlassCard
      className={cn(
        '!p-5',
        isExecuting && 'ring-1 ring-charlie-cyan/40 animate-[pulse_3s_ease-in-out_infinite]',
      )}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <StatusDot
            status={orchestratorVariant[status.status] || 'idle'}
            pulse={isExecuting}
          />
          <div>
            <h2 className="font-bold text-base text-charlie-text">Orchestrator</h2>
            <span className="text-xs text-charlie-dim capitalize">{status.status}</span>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <div className="text-center">
            <div className="text-lg font-bold text-charlie-cyan">{status.active_agents}</div>
            <div className="text-[10px] text-charlie-dim uppercase">Active</div>
          </div>
        </div>
      </div>

      {status.current_plan && (
        <div className="mt-3 pt-3 border-t border-charlie-border/30">
          <span className="text-xs text-charlie-dim">Current plan:</span>
          <p className="text-sm text-charlie-text mt-1">{status.current_plan}</p>
        </div>
      )}
    </GlassCard>
  )
}

function AgentCard({ agent }: { agent: AgentInfo }) {
  return (
    <GlassCard className="!p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <StatusDot
            status={agent.status === 'busy' ? 'online' : agent.status === 'error' ? 'error' : 'idle'}
            pulse={agent.status === 'busy'}
          />
          <span className="font-semibold text-sm text-charlie-text font-display tracking-wide">{agent.name}</span>
        </div>
        <Badge variant={agentStatusVariant[agent.status] || 'dim'}>
          {agent.status.toUpperCase()}
        </Badge>
      </div>

      <div className="text-xs text-charlie-dim mb-1 font-body">Role: {agent.role}</div>

      {agent.current_task && (
        <div className="mt-2 p-2 rounded bg-charlie-dark/40 border border-charlie-border/30">
          <span className="text-[10px] text-charlie-dim uppercase font-display tracking-wider">Current Task</span>
          <p className="text-xs text-charlie-text mt-0.5 font-mono">{agent.current_task}</p>
        </div>
      )}
    </GlassCard>
  )
}

export default function AgentsPage() {
  const [data, setData] = useState<AgentStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState<'cards' | 'network'>('cards')
  const layout = useAutoLayout()

  const loadStatus = useCallback(async () => {
    try {
      const status = await api.fetchAgentStatus()
      setData(status)
    } catch (e) {
      console.error('Failed to load agent status:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  // Build graph from agent data
  const { nodes, edges } = useMemo(() => {
    if (!data) return { nodes: [], edges: [] }

    const orchestratorNode: Node = {
      id: 'orchestrator',
      type: 'agent',
      position: { x: 0, y: 0 },
      data: {
        label: 'Orchestrator',
        role: 'coordinator',
        status: data.orchestrator.status === 'executing' ? 'running' : data.orchestrator.status === 'error' ? 'error' : 'idle',
        currentTask: data.orchestrator.current_plan || undefined,
      },
    }

    const agentNodes: Node[] = data.agents.map((agent) => ({
      id: agent.id,
      type: 'agent',
      position: { x: 0, y: 0 },
      data: {
        label: agent.name,
        role: agent.role,
        status: agent.status === 'busy' ? 'running' : agent.status === 'error' ? 'error' : 'idle',
        currentTask: agent.current_task || undefined,
      },
    }))

    const graphEdges: Edge[] = data.agents.map((agent) => ({
      id: `e-orch-${agent.id}`,
      source: 'orchestrator',
      target: agent.id,
      style: {
        stroke: agent.status === 'busy' ? 'rgba(136, 204, 255, 0.4)' : 'rgba(136, 204, 255, 0.15)',
        strokeWidth: agent.status === 'busy' ? 2 : 1,
      },
    }))

    return layout([orchestratorNode, ...agentNodes], graphEdges, { direction: 'TB', rankSep: 120 })
  }, [data, layout])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <LoadingSpinner label="Loading agent status..." />
      </div>
    )
  }

  if (!data) {
    return <EmptyState title="Failed to load agents" description="Could not fetch agent status" />
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Agents"
        subtitle={`${data.agents.length} agents registered`}
        actions={
          <div className="flex gap-1 bg-charlie-card rounded-lg p-0.5 border border-charlie-border">
            <button
              onClick={() => setView('cards')}
              className={`px-3 py-1 rounded text-xs cursor-pointer transition-colors ${view === 'cards' ? 'bg-charlie-cyan/15 text-charlie-cyan' : 'text-charlie-dim hover:text-charlie-text'}`}
            >
              Cards
            </button>
            <button
              onClick={() => setView('network')}
              className={`px-3 py-1 rounded text-xs cursor-pointer transition-colors ${view === 'network' ? 'bg-charlie-cyan/15 text-charlie-cyan' : 'text-charlie-dim hover:text-charlie-text'}`}
            >
              Network
            </button>
          </div>
        }
      />

      {view === 'cards' ? (
        <div className="space-y-6">
          <OrchestratorCard status={data.orchestrator} />
          {data.agents.length === 0 ? (
            <EmptyState title="No agents" description="No agents are currently registered" />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {data.agents.map((agent) => (
                <AgentCard key={agent.id} agent={agent} />
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="h-[calc(100vh-240px)] rounded-xl border border-charlie-border overflow-hidden">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            proOptions={{ hideAttribution: true }}
            style={{ background: 'transparent' }}
          >
            <Background color="rgba(136, 204, 255, 0.05)" gap={20} />
            <Controls className="!bg-charlie-card !border-charlie-border !shadow-none" />
          </ReactFlow>
        </div>
      )}
    </div>
  )
}
