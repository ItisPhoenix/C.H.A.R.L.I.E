'use client'

import { useEffect, useState } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/layout/PageHeader'
import { fetchBriefing, runBriefing } from '@/lib/api'
import { formatTimestamp } from '@/lib/utils'
import type { BriefingData } from '@/lib/types'

export default function BriefingPage() {
  const [briefing, setBriefing] = useState<BriefingData | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    loadBriefing()
  }, [])

  async function loadBriefing() {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchBriefing()
      setBriefing(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load briefing')
    }
    setLoading(false)
  }

  async function handleGenerate() {
    setGenerating(true)
    setError(null)
    try {
      const data = await runBriefing()
      setBriefing(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to generate briefing')
    }
    setGenerating(false)
  }

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <PageHeader
        title="Daily Briefing"
        subtitle={
          briefing?.assembled_at
            ? `Assembled ${formatTimestamp(briefing.assembled_at)}`
            : undefined
        }
        actions={
          <Button
            variant="primary"
            size="md"
            loading={generating}
            onClick={handleGenerate}
          >
            Generate Briefing
          </Button>
        }
      />

      {loading ? (
        <GlassCard>
          <div className="flex justify-center py-8">
            <LoadingSpinner label="Loading briefing..." />
          </div>
        </GlassCard>
      ) : error ? (
        <GlassCard>
          <p className="text-charlie-red text-sm text-center py-4">{error}</p>
          <div className="text-center mt-2">
            <Button variant="ghost" size="sm" onClick={loadBriefing}>
              Retry
            </Button>
          </div>
        </GlassCard>
      ) : generating ? (
        <GlassCard>
          <div className="flex justify-center py-8">
            <LoadingSpinner label="Assembling briefing..." />
          </div>
        </GlassCard>
      ) : !briefing ? (
        <EmptyState title="No briefing available" description="Click Generate Briefing to create one." />
      ) : (
        <div className="space-y-4">
          <AgendaSection agenda={briefing.agenda} />
          <HealthSection health={briefing.health} />
          <TasksSection tasks={briefing.tasks} />
          <IntelSection intel={briefing.intel} />
          <ContextSection context={briefing.context} />
        </div>
      )}
    </div>
  )
}

function AgendaSection({ agenda }: { agenda: BriefingData['agenda'] }) {
  return (
    <GlassCard>
      <h3 className="text-sm font-semibold text-charlie-cyan mb-3 font-display tracking-[0.1em] uppercase">Agenda</h3>
      {!agenda?.events || agenda.events.length === 0 ? (
        <p className="text-charlie-dim text-sm">No events scheduled.</p>
      ) : (
        <div className="space-y-2">
          {agenda.events.map((event, i) => (
            <div key={i} className="flex items-center gap-3 text-sm">
              <span className="text-charlie-cyan font-mono w-16 shrink-0">{event.time}</span>
              <span className="text-charlie-text">{event.title}</span>
            </div>
          ))}
        </div>
      )}
    </GlassCard>
  )
}

function HealthSection({ health }: { health: BriefingData['health'] }) {
  const vitals = health?.vitals || {}

  return (
    <GlassCard>
      <h3 className="text-sm font-semibold text-charlie-cyan mb-3 font-display tracking-[0.1em] uppercase">System Health</h3>
      <div className="grid grid-cols-2 gap-3 text-sm">
        {Object.entries(vitals).map(([key, value]) => (
          <div key={key} className="flex justify-between">
            <span className="text-charlie-dim capitalize">{key.replace(/_/g, ' ')}</span>
            <span className="text-charlie-text">{String(value)}</span>
          </div>
        ))}
        <div className="flex justify-between">
          <span className="text-charlie-dim">Restart Count</span>
          <Badge variant={health?.restarts && health.restarts > 0 ? 'amber' : 'green'}>
            {health?.restarts ?? 0}
          </Badge>
        </div>
      </div>
    </GlassCard>
  )
}

function TasksSection({ tasks }: { tasks: BriefingData['tasks'] }) {
  const pending = tasks?.pending || []

  return (
    <GlassCard>
      <h3 className="text-sm font-semibold text-charlie-cyan mb-3 font-display tracking-[0.1em] uppercase">
        Pending Tasks
        <Badge variant="amber" className="ml-2">
          {tasks?.count ?? 0}
        </Badge>
      </h3>
      {pending.length === 0 ? (
        <p className="text-charlie-dim text-sm">No pending tasks.</p>
      ) : (
        <div className="space-y-2">
          {pending.map((task, i) => (
            <div key={i} className="flex items-center justify-between text-sm">
              <span className="text-charlie-text">{task.name}</span>
              <Badge variant={task.priority >= 3 ? 'red' : task.priority >= 2 ? 'amber' : 'dim'}>
                P{task.priority}
              </Badge>
            </div>
          ))}
        </div>
      )}
    </GlassCard>
  )
}

function IntelSection({ intel }: { intel: BriefingData['intel'] }) {
  const news = intel?.news || []

  return (
    <GlassCard>
      <h3 className="text-sm font-semibold text-charlie-cyan mb-3 font-display tracking-[0.1em] uppercase">Intel</h3>
      {news.length === 0 ? (
        <p className="text-charlie-dim text-sm">No news items.</p>
      ) : (
        <div className="space-y-2">
          {news.map((item, i) => (
            <div key={i} className="flex items-start gap-2 text-sm">
              <span className="text-charlie-dim text-xs mt-0.5 shrink-0">{item.source}</span>
              <span className="text-charlie-text">{item.title}</span>
            </div>
          ))}
        </div>
      )}
    </GlassCard>
  )
}

function ContextSection({ context }: { context: BriefingData['context'] }) {
  const world = context?.world_state

  return (
    <GlassCard>
      <h3 className="text-sm font-semibold text-charlie-cyan mb-3 font-display tracking-[0.1em] uppercase">Context</h3>
      {world ? (
        <div className="grid grid-cols-3 gap-3 text-sm mb-3">
          <div>
            <span className="text-charlie-dim block text-xs">Active App</span>
            <span className="text-charlie-text">{world.active_app || 'Unknown'}</span>
          </div>
          <div>
            <span className="text-charlie-dim block text-xs">Current Task</span>
            <span className="text-charlie-text">{world.task || 'None'}</span>
          </div>
          <div>
            <span className="text-charlie-dim block text-xs">Frustration</span>
            <Badge
              variant={
                world.frustration > 0.7 ? 'red' : world.frustration > 0.3 ? 'amber' : 'green'
              }
            >
              {(world.frustration * 100).toFixed(0)}%
            </Badge>
          </div>
        </div>
      ) : (
        <p className="text-charlie-dim text-sm mb-3">No world state available.</p>
      )}

      {context?.recent_conversation && context.recent_conversation.length > 0 && (
        <div>
          <span className="text-charlie-dim text-xs block mb-1">Recent Conversation</span>
          <p className="text-charlie-text text-sm line-clamp-3">
            {String(context.recent_conversation[0])}
          </p>
        </div>
      )}
    </GlassCard>
  )
}
