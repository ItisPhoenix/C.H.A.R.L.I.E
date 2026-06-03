'use client'

import { useEffect, useState, useCallback, useMemo } from 'react'
import dynamic from 'next/dynamic'

const ReactFlow = dynamic(() => import('@xyflow/react').then(m => m.ReactFlow), { ssr: false })
const Background = dynamic(() => import('@xyflow/react').then(m => m.Background), { ssr: false })
const Controls = dynamic(() => import('@xyflow/react').then(m => m.Controls), { ssr: false })

import '@xyflow/react/dist/style.css'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { StatusDot } from '@/components/ui/StatusDot'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorState } from '@/components/ui/ErrorState'
import { PageHeader } from '@/components/layout/PageHeader'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { Modal } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { AgentNode } from '@/components/graphs/AgentNode'
import { useAutoLayout } from '@/components/graphs/useAutoLayout'
import { cn } from '@/lib/utils'
import { useWSEvent } from '@/lib/ws'
import { Plus, Trash2, Pencil, ChevronDown, ChevronRight } from 'lucide-react'
import * as api from '@/lib/api'
import { FilterBar } from '@/components/ui/FilterBar'
import type { AgentStatus, AgentInfo } from '@/lib/types'
import type { Node, Edge } from '@xyflow/react'

const nodeTypes = { agent: AgentNode }

const AGENT_MANIFESTS = [
  { id: 'coding', name: 'coding', description: 'Code analysis, debugging, building, testing, git operations', tools: ['run_command', 'read_file', 'write_file', 'code_analyze', 'code_search', 'search_files'], skills: ['python-debugging'], icon: '💻' },
  { id: 'comms', name: 'comms', description: 'Email, notifications, messaging, calendar management', tools: ['send_gmail', 'get_gmail_messages', 'send_file_to_mobile', 'get_calendar_events', 'manage_calendar'], skills: [], icon: '💬' },
  { id: 'redteam', name: 'redteam', description: 'Ethical hacking, penetration testing, CTF/HTB assistance', tools: ['whois_lookup', 'dns_enum', 'subdomain_scan', 'tech_fingerprint', 'google_dork', 'scan_target', 'fuzz_dirs', 'analyze_vuln', 'generate_payload', 'write_report', 'ctf_hint', 'explain_exploit'], skills: ['nmap-mastery', 'web-exploitation'], icon: '🔴' },
  { id: 'research', name: 'research', description: 'Web research, search engines, browser fetching, news analysis', tools: ['search', 'browser_fetch', 'get_news', 'read_file', 'code_analyze', 'code_search'], skills: ['deep-research', 'source-verification'], icon: '🔍' },
  { id: 'system', name: 'system', description: 'PC control, processes, app management, system monitoring', tools: ['run_command', 'get_pc_status', 'get_system_status', 'get_active_processes', 'open_app', 'open_website', 'set_volume', 'control_media', 'press_key', 'type_text'], skills: [], icon: '⚙️' },
  { id: 'vision', name: 'vision', description: 'Screen analysis, image understanding, OCR, visual inspection', tools: ['analyze_screen', 'describe_image', 'read_screen_text', 'screenshot_save', 'capture_webcam'], skills: [], icon: '👁️' },
  { id: 'writer', name: 'writer', description: 'File editing, code changes, documentation, content creation', tools: ['read_file', 'write_file', 'list_files', 'search_files', 'calculate'], skills: ['report-writing'], icon: '✏️' },
]

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
            <div className="text-xs text-charlie-dim uppercase">Active</div>
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

function AgentCard({ agent, onDelete, deleting, onEdit }: { agent: AgentInfo; onDelete?: () => void; deleting?: boolean; onEdit?: () => void }) {
  const manifest = AGENT_MANIFESTS.find((m) => m.id === agent.id)

  return (
    <GlassCard className="!p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <StatusDot
            status={agent.status === 'busy' ? 'online' : agent.status === 'error' ? 'error' : 'idle'}
            pulse={agent.status === 'busy'}
          />
          {manifest && <span className="text-base">{manifest.icon}</span>}
          <span className="font-semibold text-sm text-charlie-text font-display tracking-wide">{agent.name}</span>
        </div>
        <div className="flex items-center gap-1">
          <Badge variant={agentStatusVariant[agent.status] || 'dim'}>
            {agent.status.toUpperCase()}
          </Badge>
          {onEdit && (
            <Button variant="ghost" size="xs"
              onClick={onEdit} title="Edit agent"
              className="text-charlie-dim hover:text-charlie-cyan">
              <Pencil size={12} />
            </Button>
          )}
          {onDelete && (
            <Button variant="danger" size="xs" loading={deleting}
              onClick={onDelete} title="Delete agent">
              <Trash2 size={12} />
            </Button>
          )}
        </div>
      </div>

      <div className="text-xs text-charlie-dim mb-2 font-body">{agent.role}</div>

      {manifest && (
        <div className="mt-2 space-y-2">
          {manifest.tools.length > 0 && (
            <div>
              <span className="text-xs text-charlie-dim font-display uppercase tracking-[0.1em]">Tools ({manifest.tools.length})</span>
              <div className="flex flex-wrap gap-1 mt-1">
                {manifest.tools.map((t) => (
                  <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-charlie-cyan/10 text-charlie-cyan border border-charlie-cyan/20 font-mono">{t}</span>
                ))}
              </div>
            </div>
          )}
          {manifest.skills.length > 0 && (
            <div>
              <span className="text-xs text-charlie-dim font-display uppercase tracking-[0.1em]">Skills ({manifest.skills.length})</span>
              <div className="flex flex-wrap gap-1 mt-1">
                {manifest.skills.map((s) => (
                  <span key={s} className="text-[10px] px-1.5 py-0.5 rounded bg-charlie-green/10 text-charlie-green border border-charlie-green/20 font-mono">{s}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {agent.current_task && (
        <div className="mt-2 p-2 rounded-lg bg-charlie-dark/40 border border-charlie-border/30">
          <span className="text-xs text-charlie-dim uppercase font-display tracking-[0.1em]">Current Task</span>
          <p className="text-xs text-charlie-text mt-0.5 font-mono">{agent.current_task}</p>
        </div>
      )}
    </GlassCard>
  )
}

function CreateAgentModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [tools, setTools] = useState('')
  const [skills, setSkills] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSave = async () => {
    if (!name.trim()) { setError('Name required'); return }
    setSaving(true)
    setError(null)
    try {
      const data: Record<string, unknown> = {
        name: name.trim(),
        description: description.trim(),
      }
      if (tools.trim()) data.tools = tools.split(',').map(t => t.trim()).filter(Boolean)
      if (skills.trim()) data.skills = skills.split(',').map(s => s.trim()).filter(Boolean)
      const res = await api.createAgent(data)
      if (res.ok) { onCreated(); onClose() }
      else { setError(res.error || 'Failed to create agent') }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create agent')
    } finally { setSaving(false) }
  }

  return (
    <Modal open onClose={onClose} title="Create Agent">
      <div className="space-y-4">
        {error && <p className="text-sm text-charlie-red">{error}</p>}
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
            placeholder="my-agent" />
        </div>
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Description</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none min-h-[80px]"
            placeholder="What does this agent do?" />
        </div>
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Tools (comma-separated)</label>
          <input value={tools} onChange={(e) => setTools(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
            placeholder="run_command, read_file, write_file" />
        </div>
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Skills (comma-separated)</label>
          <input value={skills} onChange={(e) => setSkills(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
            placeholder="python-debugging, web-exploitation" />
        </div>
        <div className="flex gap-2 justify-end">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" loading={saving} onClick={handleSave}>Create</Button>
        </div>
      </div>
    </Modal>
  )
}

function EditAgentModal({ agent, manifest, onClose, onSaved }: {
  agent: AgentInfo
  manifest?: typeof AGENT_MANIFESTS[number]
  onClose: () => void
  onSaved: () => void
}) {
  const [description, setDescription] = useState(manifest?.description || agent.role || '')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [tools, setTools] = useState(manifest?.tools.join(', ') || '')
  const [showSystemPrompt, setShowSystemPrompt] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const data: Record<string, unknown> = {
        description: description.trim(),
      }
      if (tools.trim()) data.tools = tools.split(',').map(t => t.trim()).filter(Boolean)
      if (systemPrompt.trim()) data.system_prompt = systemPrompt.trim()
      const res = await api.updateAgent(agent.name, data)
      if (res.ok) { onSaved(); onClose() }
      else { setError(res.error || 'Failed to update agent') }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update agent')
    } finally { setSaving(false) }
  }

  return (
    <Modal open onClose={onClose} title={`Edit Agent: ${agent.name}`}>
      <div className="space-y-4">
        {error && <p className="text-sm text-charlie-red">{error}</p>}
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Name</label>
          <input value={agent.name} readOnly
            className="w-full bg-charlie-dark/60 border border-charlie-border rounded-lg p-2 text-sm text-charlie-dim cursor-not-allowed" />
        </div>
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Description</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none min-h-[80px]"
            placeholder="What does this agent do?" />
        </div>
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">System Prompt</label>
          <button
            type="button"
            onClick={() => setShowSystemPrompt(!showSystemPrompt)}
            className="flex items-center gap-1 text-xs text-charlie-cyan hover:text-charlie-cyan/80 transition-colors"
          >
            {showSystemPrompt ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            {showSystemPrompt ? 'Hide system prompt' : 'Show system prompt'}
          </button>
          {showSystemPrompt && (
            <textarea value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)}
              className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none min-h-[200px] mt-2 font-mono text-xs"
              placeholder="Enter a system prompt for this agent..." />
          )}
        </div>
        <div>
          <label className="text-xs text-charlie-dim mb-1 block">Tools (comma-separated)</label>
          <input value={tools} onChange={(e) => setTools(e.target.value)}
            className="w-full bg-charlie-dark border border-charlie-border rounded-lg p-2 text-sm text-charlie-text focus:border-charlie-cyan focus:outline-none"
            placeholder="run_command, read_file, write_file" />
        </div>
        <div className="flex gap-2 justify-end">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" loading={saving} onClick={handleSave}>Save Changes</Button>
        </div>
      </div>
    </Modal>
  )
}

function LearningTab() {
  const [stats, setStats] = useState<{ total_records: number; overall_success_rate: number; agents: Record<string, { total: number; successes: number; success_rate: number }> } | null>(null)
  const [history, setHistory] = useState<Array<{ agent: string; keywords: string[]; success: boolean; duration_ms: number; timestamp: number }>>([])
  const [loading, setLoading] = useState(true)
  const [goal, setGoal] = useState('')
  const [planning, setPlanning] = useState(false)
  const [planResult, setPlanResult] = useState<Array<{ id: string; description: string; suggested_agent: string; status: string }> | null>(null)

  useEffect(() => {
    async function load() {
      try {
        const data = await api.fetchOrchestratorLearning()
        if (data.ok) {
          setStats(data.stats || null)
          setHistory(data.history || [])
        }
      } catch (e) {
        console.error('Failed to load learning data:', e)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  async function handlePlan() {
    if (!goal.trim()) return
    setPlanning(true)
    try {
      const result = await api.planOrchestrator(goal)
      if (result.ok && result.subtasks) {
        setPlanResult(result.subtasks)
      }
    } catch (e) {
      console.error('Failed to plan:', e)
    } finally {
      setPlanning(false)
    }
  }

  if (loading) return <LoadingSpinner label="Loading learning data..." />

  return (
    <div className="space-y-6">
      {/* Plan a Goal */}
      <GlassCard>
        <h3 className="font-display text-sm text-charlie-cyan tracking-[0.1em] uppercase mb-4">Plan a Goal</h3>
        <div className="flex gap-3">
          <input
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="Describe a complex goal to decompose..."
            className="flex-1 bg-charlie-card border border-charlie-border rounded-lg px-3 py-2 text-sm text-charlie-text placeholder:text-charlie-dim/50"
            onKeyDown={(e) => e.key === 'Enter' && handlePlan()}
          />
          <Button onClick={handlePlan} disabled={planning || !goal.trim()}>
            {planning ? 'Planning...' : 'Plan'}
          </Button>
        </div>
        {planResult && (
          <div className="mt-4 space-y-2">
            {planResult.map((task) => (
              <div key={task.id} className="flex items-center gap-3 p-3 rounded-lg bg-charlie-card border border-charlie-border">
                <Badge variant={task.status === 'done' ? 'green' : task.status === 'failed' ? 'red' : 'cyan'}>{task.id}</Badge>
                <span className="text-sm text-charlie-text flex-1">{task.description}</span>
                <Badge variant="dim">{task.suggested_agent}</Badge>
              </div>
            ))}
          </div>
        )}
      </GlassCard>

      {/* Agent Success Rates */}
      <GlassCard>
        <h3 className="font-display text-sm text-charlie-cyan tracking-[0.1em] uppercase mb-4">Agent Success Rates</h3>
        {stats && Object.keys(stats.agents).length > 0 ? (
          <div className="space-y-3">
            {Object.entries(stats.agents).map(([name, data]) => (
              <div key={name} className="flex items-center gap-4">
                <span className="text-sm text-charlie-text w-24 truncate" title={name}>{name}</span>
                <div className="flex-1 h-2 bg-charlie-border rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${data.success_rate * 100}%`,
                      background: data.success_rate > 0.7 ? 'var(--charlie-green)' : data.success_rate > 0.4 ? 'var(--charlie-amber)' : 'var(--charlie-red)',
                    }}
                  />
                </div>
                <span className="text-xs text-charlie-dim w-12 text-right">{(data.success_rate * 100).toFixed(0)}%</span>
                <span className="text-xs text-charlie-dim w-16 text-right">{data.total} tasks</span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState title="No learning data" description="Agent success rates will appear here after tasks are executed" />
        )}
      </GlassCard>

      {/* Recent History */}
      <GlassCard>
        <h3 className="font-display text-sm text-charlie-cyan tracking-[0.1em] uppercase mb-4">Recent Task History</h3>
        {history.length > 0 ? (
          <div className="space-y-2">
            {history.slice(0, 20).map((entry, i) => (
              <div key={i} className="flex items-center gap-3 p-2 rounded-lg bg-charlie-card/50">
                <StatusDot status={entry.success ? 'online' : 'error'} />
                <span className="text-sm text-charlie-text w-20 truncate">{entry.agent}</span>
                <span className="text-xs text-charlie-dim flex-1 truncate">{entry.keywords.join(', ')}</span>
                <span className="text-xs text-charlie-dim">{entry.duration_ms.toFixed(0)}ms</span>
                <span className="text-xs text-charlie-dim">{new Date(entry.timestamp * 1000).toLocaleTimeString()}</span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState title="No history" description="Task execution history will appear here" />
        )}
      </GlassCard>
    </div>
  )
}

export default function AgentsPage() {
  const [data, setData] = useState<AgentStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [view, setView] = useState<'cards' | 'network' | 'learning'>('cards')
  const [showCreate, setShowCreate] = useState(false)
  const [editingAgent, setEditingAgent] = useState<AgentInfo | null>(null)
  const [deletingAgent, setDeletingAgent] = useState<string | null>(null)
  const layout = useAutoLayout()

  const buildStaticData = useCallback((): AgentStatus => ({
    orchestrator: { status: 'idle', active_agents: 0 },
    agents: AGENT_MANIFESTS.map((m) => ({
      id: m.id,
      name: m.name,
      role: m.description,
      status: 'idle' as const,
    })),
  }), [])

  const loadStatus = useCallback(async () => {
    try {
      const result = await api.fetchAgentStatus()
      if (result && result.orchestrator) {
        // Merge API data with manifest data to ensure all agents are shown
        const apiAgents = result.agents || []
        const mergedAgents = AGENT_MANIFESTS.map((m) => {
          const apiAgent = apiAgents.find((a: AgentInfo) => a.id === m.id)
          return apiAgent || { id: m.id, name: m.name, role: m.description, status: 'idle' as const }
        })
        setData({ ...result, agents: mergedAgents })
      } else {
        setData(buildStaticData())
      }
    } catch {
      setData(buildStaticData())
    } finally {
      setLoading(false)
    }
  }, [buildStaticData])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  async function handleDeleteAgent(name: string) {
    setDeletingAgent(name)
    try {
      await api.deleteAgent(name)
      await loadStatus()
    } catch (e) {
      console.error('Failed to delete agent:', e)
    } finally { setDeletingAgent(null) }
  }

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

    const agentNodes: Node[] = data.agents.map((agent) => {
      const manifest = AGENT_MANIFESTS.find((m) => m.id === agent.id)
      return {
        id: agent.id,
        type: 'agent',
        position: { x: 0, y: 0 },
        data: {
          label: agent.name,
          role: agent.role,
          status: agent.status === 'busy' ? 'running' : agent.status === 'error' ? 'error' : 'idle',
          currentTask: agent.current_task || undefined,
          toolsCount: manifest?.tools.length || 0,
          skillsCount: manifest?.skills.length || 0,
        },
      }
    })

    const graphEdges: Edge[] = data.agents.map((agent) => ({
      id: `e-orch-${agent.id}`,
      source: 'orchestrator',
      target: agent.id,
      style: {
        stroke: agent.status === 'busy' ? 'color-mix(in srgb, var(--charlie-cyan) 40%, transparent)' : 'color-mix(in srgb, var(--charlie-cyan) 15%, transparent)',
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
    return <ErrorState error="Could not fetch agent status" onRetry={loadStatus} />
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <PageHeader
        title="Agents"
        subtitle={`${data.agents.length} agents registered`}
        actions={
          <div className="flex items-center gap-3">
            <Button size="sm" onClick={() => setShowCreate(true)}><Plus size={14} className="mr-1" />Create Agent</Button>
            <FilterBar options={['cards', 'network', 'learning'] as const} value={view} onChange={(v) => setView(v as typeof view)} />
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
                <AgentCard key={agent.id} agent={agent} onDelete={() => handleDeleteAgent(agent.name)} deleting={deletingAgent === agent.name} onEdit={() => setEditingAgent(agent)} />
              ))}
            </div>
          )}
        </div>
      ) : view === 'network' ? (
        <div className="h-[calc(100vh-240px)] rounded-xl border border-charlie-border overflow-hidden">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            fitView
            nodesDraggable
            nodesConnectable={false}
            panOnDrag
            zoomOnScroll
            zoomOnPinch
            proOptions={{ hideAttribution: true }}
            style={{ background: 'transparent' }}
          >
            <Background color="color-mix(in srgb, var(--charlie-cyan) 5%, transparent)" gap={20} />
            <Controls className="!bg-charlie-card !border-charlie-border !shadow-none" />
          </ReactFlow>
        </div>
      ) : (
        <LearningTab />
      )}

      {showCreate && <CreateAgentModal onClose={() => setShowCreate(false)} onCreated={loadStatus} />}
      {editingAgent && (
        <EditAgentModal
          agent={editingAgent}
          manifest={AGENT_MANIFESTS.find((m) => m.id === editingAgent.id)}
          onClose={() => setEditingAgent(null)}
          onSaved={loadStatus}
        />
      )}
    </div>
  )
}
