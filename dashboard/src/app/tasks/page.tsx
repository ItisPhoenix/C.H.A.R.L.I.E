'use client'

import { useEffect, useState, useMemo, useCallback } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/layout/PageHeader'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorState } from '@/components/ui/ErrorState'
import { fetchTasks, cancelTask } from '@/lib/api'
import { useWSEvent } from '@/lib/ws'
import { formatTimestamp, cn, createVisibilityAwareInterval } from '@/lib/utils'
import { ChevronDown } from 'lucide-react'
import type { Task } from '@/lib/types'

type KanbanColumn = 'pending' | 'active' | 'completed' | 'failed'

const COLUMN_CONFIG: { key: KanbanColumn; label: string; color: 'dim' | 'cyan' | 'green' | 'red' }[] = [
  { key: 'pending', label: 'Pending', color: 'dim' },
  { key: 'active', label: 'Active', color: 'cyan' },
  { key: 'completed', label: 'Completed', color: 'green' },
  { key: 'failed', label: 'Failed', color: 'red' },
]

const statusVariant: Record<string, 'cyan' | 'green' | 'red' | 'dim' | 'amber'> = {
  running: 'cyan',
  pending: 'dim',
  completed: 'green',
  failed: 'red',
  blocked: 'amber',
}

function getTaskColumn(task: Task): KanbanColumn {
  if (task.status === 'running') return 'active'
  if (task.status === 'pending') return 'pending'
  if (task.status === 'completed') return 'completed'
  if (task.status === 'failed') return 'failed'
  return 'pending'
}

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const loadTasks = useCallback(async () => {
    try {
      setError(null)
      const data = await fetchTasks()
      setTasks(data.tasks)
    } catch (e) {
      console.error('Failed to load tasks:', e)
      setError('Failed to load tasks')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadTasks()
    return createVisibilityAwareInterval(loadTasks, 1000)
  }, [loadTasks])

  const taskUpdate = useWSEvent('task_update')
  useEffect(() => {
    if (taskUpdate) loadTasks()
  }, [taskUpdate])

  async function handleCancel(id: string) {
    setCancelling(id)
    try {
      await cancelTask(id)
      await loadTasks()
    } finally {
      setCancelling(null)
    }
  }

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const columns = useMemo(() => {
    const cols: Record<KanbanColumn, Task[]> = {
      pending: [], active: [], completed: [], failed: [],
    }
    for (const task of tasks) {
      cols[getTaskColumn(task)].push(task)
    }
    return cols
  }, [tasks])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <LoadingSpinner label="Loading tasks..." />
      </div>
    )
  }

  if (error) {
    return <ErrorState error={error} onRetry={loadTasks} />
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <PageHeader title="Tasks" subtitle={`${tasks.length} total tasks`} />

      {/* Kanban columns */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {COLUMN_CONFIG.map((col) => {
          const items = columns[col.key]
          return (
            <div key={col.key} className="space-y-3">
              {/* Column header */}
              <div className="flex items-center justify-between pb-2 border-b border-charlie-border">
                <h3 className="font-display text-sm tracking-[0.1em] text-charlie-cyan uppercase">
                  {col.label}
                </h3>
                <Badge variant={col.color}>{items.length}</Badge>
              </div>

              {/* Column items */}
              {items.length === 0 ? (
                <GlassCard className="!p-4">
                  <EmptyState terminal title={`No ${col.label.toLowerCase()} tasks`} />
                </GlassCard>
              ) : (
                items.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    cancelling={cancelling}
                    expanded={expanded.has(task.id)}
                    onToggle={() => toggleExpanded(task.id)}
                    onCancel={() => handleCancel(task.id)}
                  />
                ))
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// --- TaskCard ---

interface TaskCardProps {
  task: Task
  cancelling: string | null
  expanded: boolean
  onToggle: () => void
  onCancel: () => void
}

function TaskCard({ task, cancelling, expanded, onToggle, onCancel }: TaskCardProps) {
  const hasDetail = !!(task.result || task.error)

  return (
      <GlassCard className="hover:shadow-neon-cyan-sm transition-all !p-0 overflow-hidden">
        <div
          className={cn('p-4', hasDetail && 'cursor-pointer')}
          onClick={hasDetail ? onToggle : undefined}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="font-semibold text-sm text-charlie-text truncate font-body">
                  {task.name}
                </span>
                <Badge variant={statusVariant[task.status] || 'dim'}>
                  {task.status === 'running' && (
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-charlie-cyan mr-1 animate-pulse" />
                  )}
                  {task.status.toUpperCase()}
                </Badge>
              </div>
              <div className="flex items-center gap-3 text-xs text-charlie-dim mt-1 font-mono">
                <span>P{task.priority}</span>
                <span>{formatTimestamp(task.created_at)}</span>
              </div>
              {task.dependencies.length > 0 && (
                <div className="mt-2 flex items-center gap-1 flex-wrap">
                  <span className="text-xs text-charlie-dim">Deps:</span>
                  {task.dependencies.map((dep) => (
                    <Badge key={dep} variant="dim" className="text-xs">{dep}</Badge>
                  ))}
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              {task.status === 'running' && (
                <Button
                  variant="danger"
                  size="sm"
                  loading={cancelling === task.id}
                  onClick={(e) => { e.stopPropagation(); onCancel() }}
                >
                  Cancel
                </Button>
              )}
              {hasDetail && (
                <ChevronDown size={16} className={cn('text-charlie-dim transition-transform shrink-0', expanded && 'rotate-180')} />
              )}
            </div>
          </div>
        </div>

        {expanded && hasDetail && (
          <div className="px-4 pb-4 border-t border-charlie-border/30 pt-3">
            {task.result && (
              <div className="mb-2">
                <span className="text-xs text-charlie-dim block mb-1 font-display tracking-[0.1em] uppercase">Result</span>
                <p className="text-xs text-charlie-green/80 whitespace-pre-wrap break-words font-mono">
                  {task.result}
                </p>
              </div>
            )}
            {task.error && (
              <div>
                <span className="text-xs text-charlie-dim block mb-1 font-display tracking-[0.1em] uppercase">Error</span>
                <p className="text-xs text-charlie-red/80 whitespace-pre-wrap break-words font-mono">
                  {task.error}
                </p>
              </div>
            )}
          </div>
        )}
      </GlassCard>
  )
}
