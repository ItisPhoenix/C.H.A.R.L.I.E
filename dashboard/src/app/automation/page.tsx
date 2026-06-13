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
import { ErrorState } from '@/components/ui/ErrorState'
import { PageHeader } from '@/components/layout/PageHeader'
import { Modal } from '@/components/ui/Modal'
import { FilterBar } from '@/components/ui/FilterBar'
import { FlowNode } from '@/components/graphs/FlowNode'
import { useAutoLayout } from '@/components/graphs/useAutoLayout'
import { fetchAutomationRules, toggleAutomationRule, createAutomationRule, deleteAutomationRule } from '@/lib/api'
import { riskTierLabel, cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { ChevronDown, Plus, Trash2 } from 'lucide-react'
import type { AutomationRule } from '@/lib/types'
import type { Node, Edge } from '@xyflow/react'

const nodeTypes = { flow: FlowNode }

function CreateRuleModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState('')
  const [trigger, setTrigger] = useState('')
  const [condition, setCondition] = useState('')
  const [action, setAction] = useState('')
  const [riskTier, setRiskTier] = useState(0)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSave = async () => {
    if (!name.trim()) { setError('Name required'); return }
    if (!trigger.trim()) { setError('Trigger required'); return }
    if (!action.trim()) { setError('Action required'); return }
    setSaving(true)
    setError(null)
    try {
      const res = await createAutomationRule({
        name: name.trim(),
        trigger: trigger.trim(),
        condition: condition.trim(),
        action: action.trim(),
        risk_tier: riskTier,
      })
      if (res.ok) { onCreated(); onClose() }
      else { setError(res.error || 'Failed to create rule') }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create rule')
    } finally { setSaving(false) }
  }

  return (
    <Modal open onClose={onClose} title="Create Automation Rule">
      <div className="space-y-4">
        {error && <p className="text-sm text-charlie-red">{error}</p>}
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
            placeholder="my-rule" />
        </div>
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Trigger</label>
          <input value={trigger} onChange={(e) => setTrigger(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
            placeholder="file_changed, schedule, webhook..." />
        </div>
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Condition</label>
          <input value={condition} onChange={(e) => setCondition(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
            placeholder="Optional condition expression" />
        </div>
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Action</label>
          <input value={action} onChange={(e) => setAction(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
            placeholder="notify, run_command, deploy..." />
        </div>
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Risk Tier (0-3)</label>
          <div className="flex gap-2">
            {[0, 1, 2, 3].map((tier) => (
              <button key={tier} type="button" onClick={() => setRiskTier(tier)}
                className={cn(
                  'px-3 py-1.5 rounded-lg text-xs font-mono border transition-all cursor-pointer',
                  riskTier === tier
                    ? 'bg-charlie-cyan/20 border-charlie-cyan/40 text-charlie-cyan'
                    : 'bg-charlie-dark border-charlie-border text-charlie-dim hover:text-charlie-text',
                )}>
                {tier}
              </button>
            ))}
          </div>
        </div>
        <div className="flex gap-2 justify-end">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" loading={saving} onClick={handleSave}>Create</Button>
        </div>
      </div>
    </Modal>
  )
}

export default function AutomationPage() {
  const [rules, setRules] = useState<AutomationRule[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [view, setView] = useState<'list' | 'flow'>('list')
  const [showCreate, setShowCreate] = useState(false)
  const [deleting, setDeleting] = useState<string | null>(null)
  const layout = useAutoLayout()

  useEffect(() => {
    loadRules()
  }, [])

  async function loadRules() {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchAutomationRules()
      setRules(data.rules || [])
    } catch (e) {
      console.error('Failed to load automation rules:', e)
      setError('Failed to load automation rules')
      setRules([])
    } finally {
      setLoading(false)
    }
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

  async function handleDeleteRule(name: string) {
    setDeleting(name)
    try {
      await deleteAutomationRule(name)
      await loadRules()
    } catch (e) {
      console.error('Failed to delete automation rule:', e)
    } finally { setDeleting(null) }
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
        style: { stroke: rule.enabled ? 'color-mix(in srgb, var(--charlie-cyan) 30%, transparent)' : 'color-mix(in srgb, var(--charlie-dim) 20%, transparent)', strokeWidth: 1.5 },
      })
      graphEdges.push({
        id: `e-${rule.name}-ca`,
        source: `${rule.name}-condition`,
        target: `${rule.name}-action`,
        style: { stroke: rule.enabled ? 'color-mix(in srgb, var(--charlie-cyan) 30%, transparent)' : 'color-mix(in srgb, var(--charlie-dim) 20%, transparent)', strokeWidth: 1.5 },
      })
    })

    return layout(graphNodes, graphEdges, { direction: 'LR', nodeSep: 60, rankSep: 150 })
  }, [filtered, layout])

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <PageHeader
        title="Automation Rules"
        subtitle={`${rules.length} rules configured`}
        actions={
          <div className="flex items-center gap-3">
            <Button size="sm" onClick={() => setShowCreate(true)}><Plus size={14} className="mr-1" />Create Rule</Button>
            <SearchInput
              value={search}
              onChange={setSearch}
              placeholder="Filter by name or trigger..."
              className="w-64"
            />
            <FilterBar options={['list', 'flow'] as const} value={view} onChange={(v) => setView(v as typeof view)} />
          </div>
        }
      />

      {loading ? (
        <div className="flex items-center justify-center h-[60vh]">
          <LoadingSpinner label="Loading automation rules..." />
        </div>
      ) : error ? (
        <ErrorState error={error} onRetry={loadRules} />
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
            <Background color="color-mix(in srgb, var(--charlie-cyan) 5%, transparent)" gap={20} />
            <Controls className="!bg-charlie-card !border-charlie-border !shadow-none" />
          </ReactFlow>
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((rule) => {
            const tier = riskTierLabel(rule.risk_tier)
            const isOpen = expanded.has(rule.name)

            return (
                <GlassCard key={rule.name} className="!p-0 hover:shadow-premium transition-all">
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
                        <Button
                          variant="danger"
                          size="xs"
                          loading={deleting === rule.name}
                          onClick={() => handleDeleteRule(rule.name)}
                          title="Delete rule"
                        >
                          <Trash2 size={12} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="xs"
                          onClick={() => toggleExpand(rule.name)}
                          title={isOpen ? 'Collapse rule details' : 'Expand rule details'}
                          aria-label={isOpen ? 'Collapse rule details' : 'Expand rule details'}
                          aria-expanded={isOpen}
                          className="!p-1"
                        >
                          <ChevronDown size={16} className={cn('transition-transform', isOpen && 'rotate-180')} />
                        </Button>
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
                      <div>
                        Priority:{' '}
                        <span className="text-charlie-text font-mono">{rule.priority ?? 0}</span>
                      </div>
                    </div>
                  </div>

                  {isOpen && (
                    <div className="border-t border-charlie-border px-4 py-3 space-y-2 text-xs font-body">
                      <div>
                        <span className="text-charlie-dim">Condition: </span>
                        <span className="text-charlie-text font-mono">{rule.condition}</span>
                      </div>
                      <div>
                        <span className="text-charlie-dim">Priority: </span>
                        <span className="text-charlie-text font-mono">{rule.priority ?? 0}</span>
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

      {showCreate && <CreateRuleModal onClose={() => setShowCreate(false)} onCreated={loadRules} />}
    </div>
  )
}

const PRIORITY_LEVELS = ['auto', 'low', 'medium', 'high', 'critical'] as const
const PRIORITY_COLORS: Record<string, string> = {
  auto: 'text-charlie-dim',
  low: 'text-charlie-green',
  medium: 'text-charlie-amber',
  high: 'text-charlie-orange',
  critical: 'text-charlie-red',
}

function PriorityToggle({ ruleName, value }: { ruleName: string; value: string }) {
  const [current, setCurrent] = useState(value)
  const idx = PRIORITY_LEVELS.indexOf(current as typeof PRIORITY_LEVELS[number])

  function cycle() {
    const next = PRIORITY_LEVELS[(idx + 1) % PRIORITY_LEVELS.length]
    setCurrent(next)
    // TODO: persist to backend when priority endpoint is available
  }

  return (
    <Button
      variant="ghost"
      size="xs"
      onClick={cycle}
      className={cn('font-mono', PRIORITY_COLORS[current] || 'text-charlie-dim')}
      title="Click to cycle priority"
    >
      {current}
    </Button>
  )
}
