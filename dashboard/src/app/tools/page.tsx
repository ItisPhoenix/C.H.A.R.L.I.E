'use client'

import { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { FilterBar } from '@/components/ui/FilterBar'
import { Toggle } from '@/components/ui/Toggle'
import { SearchInput } from '@/components/ui/SearchInput'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorState } from '@/components/ui/ErrorState'
import { PageHeader } from '@/components/layout/PageHeader'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import * as api from '@/lib/api'
import { formatTimestamp, cn, createVisibilityAwareInterval } from '@/lib/utils'
import { useWSEvent } from '@/lib/ws'
import { ChevronDown } from 'lucide-react'
import type { ToolExecution } from '@/lib/types'

type StatusFilter = 'all' | 'success' | 'error' | 'running'

const statusVariant: Record<string, 'cyan' | 'green' | 'red' | 'dim'> = {
  running: 'cyan',
  success: 'green',
  error: 'red',
}

const borderColorMap: Record<string, string> = {
  success: 'border-l-green-400',
  error: 'border-l-red-400',
  running: 'border-l-charlie-cyan',
}

function ToolRow({ exec }: { exec: ToolExecution }) {
  const [expanded, setExpanded] = useState(false)

  return (
      <div
        className={cn(
          'border-l-2 rounded-r-lg bg-charlie-card/50 mb-2 cursor-pointer transition-all hover:bg-charlie-card/80 hover:shadow-neon-cyan-sm',
          borderColorMap[exec.status] || 'border-l-charlie-border',
        )}
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
        <div className="flex items-center justify-between p-3">
          <div className="flex items-center gap-3 min-w-0">
            <Badge variant={statusVariant[exec.status] || 'dim'}>
              {exec.status === 'running' && (
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-charlie-cyan mr-1 animate-pulse" />
              )}
              {exec.status.toUpperCase()}
            </Badge>
            <span className="text-sm font-medium text-charlie-text truncate font-mono">
              {exec.tool_name}
            </span>
          </div>

          <div className="flex items-center gap-4 shrink-0">
            {exec.duration_ms !== undefined && (
              <span className="text-xs font-mono text-charlie-dim">
                {exec.duration_ms}ms
              </span>
            )}
            <span className="text-xs text-charlie-dim font-mono">
              {formatTimestamp(exec.started_at)}
            </span>
            <ChevronDown size={14} className={cn('text-charlie-dim transition-transform', expanded && 'rotate-180')} />
          </div>
        </div>

        {/* Expanded details — terminal style */}
        {expanded && (
          <div className="px-3 pb-3 space-y-2 border-t border-charlie-border/30 pt-2">
            {exec.input && Object.keys(exec.input).length > 0 && (
              <div className="terminal-block">
                <div className="terminal-header">
                  <div className="dot bg-charlie-cyan" />
                  <span className="text-charlie-dim text-xs">input</span>
                </div>
                <pre className="terminal-content text-xs overflow-x-auto">
                  {JSON.stringify(exec.input, null, 2)}
                </pre>
              </div>
            )}

            {exec.output && (
              <div className="terminal-block">
                <div className="terminal-header">
                  <div className="dot bg-charlie-green" />
                  <span className="text-charlie-dim text-xs">output</span>
                </div>
                <pre className="terminal-content text-xs overflow-x-auto max-h-40 overflow-y-auto">
                  {exec.output}
                </pre>
              </div>
            )}

            {exec.error && (
              <div className="terminal-block">
                <div className="terminal-header">
                  <div className="dot bg-charlie-red" />
                  <span className="text-charlie-red text-xs">error</span>
                </div>
                <pre className="terminal-content text-xs text-charlie-red/80 overflow-x-auto">
                  {exec.error}
                </pre>
              </div>
            )}

            {exec.agent_id && (
              <div className="text-xs text-charlie-dim font-body">
                Agent: <span className="text-charlie-text font-mono">{exec.agent_id}</span>
              </div>
            )}
          </div>
        )}
      </div>
  )
}

export default function ToolLogPage() {
  const [executions, setExecutions] = useState<ToolExecution[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [autoScroll, setAutoScroll] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)

  const loadLog = useCallback(async () => {
    try {
      setError(null)
      const data = await api.fetchToolLog()
      setExecutions(data.executions || [])
    } catch (e) {
      console.error('Failed to load tool log:', e)
      setError('Failed to load tool log')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadLog()
    return createVisibilityAwareInterval(loadLog, 1000)
  }, [loadLog])

  // Real-time WS updates for new tool executions
  const wsEvent = useWSEvent<ToolExecution>('tool_execution')
  useEffect(() => {
    if (!wsEvent) return
    setExecutions((prev) => {
      const idx = prev.findIndex((e) => e.id === wsEvent.id)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = wsEvent
        return next
      }
      return [wsEvent, ...prev]
    })
  }, [wsEvent])

  // Auto-scroll to bottom on new entries
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [executions, autoScroll])

  const filtered = useMemo(() => {
    return executions.filter((e) => {
      if (statusFilter !== 'all' && e.status !== statusFilter) return false
      if (search && !e.tool_name.toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
  }, [executions, statusFilter, search])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <LoadingSpinner label="Loading tool log..." />
      </div>
    )
  }

  if (error) {
    return (
      <div className="max-w-5xl mx-auto space-y-6">
        <PageHeader title="Tool Log" />
        <ErrorState error={error} onRetry={loadLog} />
      </div>
    )
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <PageHeader
        title="Tool Log"
        subtitle={`${executions.length} executions`}
        actions={
          <Toggle enabled={autoScroll} onChange={setAutoScroll} label="Auto-scroll" />
        }
      />

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder="Filter by tool name..."
          className="flex-1"
        />
        <FilterBar
          options={['all', 'success', 'error', 'running'] as const}
          value={statusFilter}
          onChange={setStatusFilter}
        />
      </div>

      {/* Execution feed */}
      {filtered.length === 0 ? (
        <EmptyState
          title="No tool executions"
          description={search ? 'No tools match your filter' : 'No tool executions recorded yet'}
        />
      ) : (
        <div ref={scrollRef} className="overflow-y-auto pr-1 max-h-[70vh]">
          {filtered.map((exec) => (
            <ToolRow key={exec.id} exec={exec} />
          ))}
        </div>
      )}
    </div>
  )
}
