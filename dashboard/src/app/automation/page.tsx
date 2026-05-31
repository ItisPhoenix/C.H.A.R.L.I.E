'use client'

import { useEffect, useState, useMemo } from 'react'
import { ReactFlow, Background, Controls } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { Toggle } from '@/components/ui/Toggle'
import { SearchInput } from '@/components/ui/SearchInput'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { PageHeader } from '@/components/layout/PageHeader'
import { FlowNode } from '@/components/graphs/FlowNode'
import { useAutoLayout } from '@/components/graphs/useAutoLayout'
import { fetchAutomationRules, toggleAutomationRule } from '@/lib/api'
import { riskTierLabel, cn } from '@/lib/utils'
import type { AutomationRule } from '@/lib/types'
import type { Node, Edge } from '@xyflow/react'

const nodeTypes = { flow: FlowNode }

export default function AutomationPage() {
  const [rules, setRules] = useState<AutomationRule[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [view, setView] = useState<'list' | 'flow'>('list')
  const layout = useAutoLayout()

  useEffect(() => {
    loadRules()
  }, [])

  async function loadRules() {
    setLoading(true)
    try {
      const data = await fetchAutomationRules()
      setRules(data.rules || [])
    } catch (e) {
      console.error('Failed to load automation rules:', e)
      setRules([])
    }
    setLoading(false)
  }

  function toggleExpand(name: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  async function handleToggle(name: string) {
    // Optimistic update
    setRules((prev) =>
      prev.map((r) =>
        r.name === name ? { ...r, enabled: !r.enabled } : r,
      ),
    )
    try {
      const result = await toggleAutomationRule(name)
      // Confirm with server state
      setRules((prev) =>
        prev.map((r) =>
          r.name === name ? { ...r, enabled: result.enabled } : r,
        ),
      )
    } catch (e) {
      console.error('Failed to toggle automation rule:', e)
      setRules((prev) =>
        prev.map((r) =>
          r.name === name ? { ...r, enabled: !r.enabled } : r,
        ),
      )
    }
  }

  const filtered = rules.filter((r) => {
    if (!search) return true
    const q = search.toLowerCase()
    return r.name.toLowerCase().includes(q) || r.trigger.toLowerCase().includes(q)
  })

  // Build flow graph from rules
  const { nodes, edges } = useMemo(() => {
    if (filtered.length === 0) return { nodes: [], edges: [] }

    const graphNodes: Node[] = []
    const graphEdges: Edge[] = []

    filtered.forEach((rule, i) => {
      // Trigger node
      graphNodes.push({
        id: `${rule.name}-trigger`,
        type: 'flow',
        position: { x: 0, y: 0 },
        data: { label: rule.trigger, type: 'trigger', active: rule.enabled },
      })
      // Condition node
      graphNodes.push({
        id: `${rule.name}-condition`,
        type: 'flow',
        position: { x: 0, y: 0 },
        data: { label: rule.condition, type: 'condition', description: rule.name, active: rule.enabled },
      })
      // Action node
      graphNodes.push({
        id: `${rule.name}-action`,
        type: 'flow',
        position: { x: 0, y: 0 },
        data: { label: rule.action, type: 'action', active: rule.enabled },
      })
      // Edges: trigger → condition → action
      graphEdges.push({
        id: `e-${rule.name}-tc`,
        source: `${rule.name}-trigger`,
        target: `${rule.name}-condition`,
        style: { stroke: rule.enabled ? 'rgba(136, 204, 255, 0.3)' : 'rgba(100, 116, 139, 0.2)', strokeWidth: 1.5 },
      })
      graphEdges.push({
        id: `e-${rule.name}-ca`,
        source: `${rule.name}-condition`,
        target: `${rule.name}-action`,
        style: { stroke: rule.enabled ? 'rgba(136, 204, 255, 0.3)' : 'rgba(100, 116, 139, 0.2)', strokeWidth: 1.5 },
      })
    })

    return layout(graphNodes, graphEdges, { direction: 'LR', nodeSep: 60, rankSep: 150 })
  }, [filtered, layout])

  return (
    <div className="space-y-4">
      <PageHeader
        title="Automation Rules"
        subtitle={`${rules.length} rules configured`}
        actions={
          <div className="flex items-center gap-3">
            <SearchInput
              value={search}
              onChange={setSearch}
              placeholder="Filter by name or trigger..."
              className="w-64"
            />
            <div className="flex gap-1 bg-charlie-card rounded-lg p-0.5 border border-charlie-border">
              <button
                onClick={() => setView('list')}
                className={`px-3 py-1 rounded text-xs cursor-pointer transition-colors ${view === 'list' ? 'bg-charlie-cyan/15 text-charlie-cyan' : 'text-charlie-dim hover:text-charlie-text'}`}
              >
                List
              </button>
              <button
                onClick={() => setView('flow')}
                className={`px-3 py-1 rounded text-xs cursor-pointer transition-colors ${view === 'flow' ? 'bg-charlie-cyan/15 text-charlie-cyan' : 'text-charlie-dim hover:text-charlie-text'}`}
              >
                Flow
              </button>
            </div>
          </div>
        }
      />

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <LoadingSpinner size="lg" label="Loading automation rules..." />
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          title="No automation rules"
          description={search ? 'No rules match your search.' : 'No automation rules configured yet.'}
        />
      ) : view === 'flow' ? (
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
      ) : (
        <div className="space-y-3">
          {filtered.map((rule) => {
            const tier = riskTierLabel(rule.risk_tier)
            const isOpen = expanded.has(rule.name)

            return (
                <GlassCard key={rule.name} className="!p-0 hover:shadow-neon-cyan-sm transition-all">
                  <div className="p-4">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <Toggle
                          enabled={rule.enabled}
                          onChange={() => handleToggle(rule.name)}
                        />
                        <div>
                          <span className="font-display text-sm text-charlie-text tracking-wide">
                            {rule.name}
                          </span>
                          {rule.description && (
                            <p className="text-xs text-charlie-dim mt-0.5 font-body">{rule.description}</p>
                          )}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <Badge variant={tier.color as 'cyan' | 'amber' | 'orange' | 'red' | 'dim'}>
                          {tier.label}
                        </Badge>
                        <button
                          onClick={() => toggleExpand(rule.name)}
                          aria-expanded={isOpen}
                          aria-label={isOpen ? 'Collapse rule details' : 'Expand rule details'}
                          className="text-charlie-dim hover:text-charlie-text transition-colors p-1 cursor-pointer"
                        >
                          <svg
                            className={cn('h-4 w-4 transition-transform', isOpen && 'rotate-180')}
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="currentColor"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={2}
                              d="M19 9l-7 7-7-7"
                            />
                          </svg>
                        </button>
                      </div>
                    </div>

                    <div className="flex gap-6 mt-3 text-xs text-charlie-dim font-body">
                      <div>
                        Trigger:{' '}
                        <span className="text-charlie-text font-mono">{rule.trigger}</span>
                      </div>
                      <div>
                        Action:{' '}
                        <span className="text-charlie-text font-mono">{rule.action}</span>
                      </div>
                    </div>
                  </div>

                  {isOpen && (
                    <div className="border-t border-charlie-border px-4 py-3 space-y-2 text-xs font-body">
                      <div>
                        <span className="text-charlie-dim">Condition: </span>
                        <span className="text-charlie-text font-mono">{rule.condition}</span>
                      </div>
                      {rule.action_args && Object.keys(rule.action_args).length > 0 && (
                        <div>
                          <span className="text-charlie-dim">Action Args: </span>
                          <span className="text-charlie-text font-mono">
                            {JSON.stringify(rule.action_args)}
                          </span>
                        </div>
                      )}
                    </div>
                  )}
                </GlassCard>
            )
          })}
        </div>
      )}
    </div>
  )
}
