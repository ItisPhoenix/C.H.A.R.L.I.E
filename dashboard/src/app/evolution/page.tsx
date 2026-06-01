'use client'

import { useState, useEffect, useCallback } from 'react'
import { PageHeader } from '@/components/layout/PageHeader'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorState } from '@/components/ui/ErrorState'
import { History, TrendingUp } from 'lucide-react'
import { fetchEvolution } from '@/lib/api'

interface EvolutionEntry {
  id: string
  skillName: string
  type: string
  status: string
  description: string
  diff?: string
  timestamp: string
  confidence: number
}

export default function EvolutionPage() {
  const [entries, setEntries] = useState<EvolutionEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const loadEvolution = useCallback(async () => {
    try {
      setError(null)
      setLoading(true)
      const data = await fetchEvolution()
      setEntries(data.entries || [])
    } catch (e) {
      console.error('Failed to load evolution:', e)
      setError('Failed to load evolution history')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadEvolution()
  }, [loadEvolution])

  const pendingCount = entries.filter((e) => e.status === 'pending').length

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <PageHeader
        title="Evolution"
        subtitle="Self-evolution history and skill improvements"
      />

      {/* Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <GlassCard className="p-4 text-center">
          <div className="font-display text-2xl text-charlie-cyan neon-text">{entries.length}</div>
          <div className="text-charlie-dim text-sm mt-1">Total Changes</div>
        </GlassCard>
        <GlassCard className="p-4 text-center">
          <div className="font-display text-2xl text-charlie-amber">{pendingCount}</div>
          <div className="text-charlie-dim text-sm mt-1">Pending Review</div>
        </GlassCard>
        <GlassCard className="p-4 text-center">
          <div className="font-display text-2xl text-charlie-green">
            {entries.filter((e) => e.status === 'approved').length}
          </div>
          <div className="text-charlie-dim text-sm mt-1">Approved</div>
        </GlassCard>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-[60vh]">
          <LoadingSpinner label="Loading evolution history..." />
        </div>
      ) : error ? (
        <ErrorState error={error} onRetry={loadEvolution} />
      ) : entries.length > 0 ? (
        <div className="space-y-3">
          {entries.map((entry) => (
              <GlassCard key={entry.id} className="p-4">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-charlie-cyan/10 flex items-center justify-center">
                      <TrendingUp size={16} className="text-charlie-cyan" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-charlie-text text-sm">{entry.skillName}</span>
                        <Badge variant={entry.type === 'improvement' ? 'cyan' : entry.type === 'creation' ? 'green' : 'amber'}>
                          {entry.type}
                        </Badge>
                        <Badge variant={entry.status === 'approved' ? 'green' : entry.status === 'rejected' ? 'red' : 'amber'}>
                          {entry.status}
                        </Badge>
                      </div>
                      <p className="text-charlie-dim text-sm mt-1">{entry.description}</p>
                    </div>
                  </div>
                  <div className="text-right text-xs text-charlie-dim">
                    <div>{entry.timestamp}</div>
                    <div className="mt-1">Confidence: {Math.round((entry.confidence ?? 0) * 100)}%</div>
                  </div>
                </div>

                {expandedId === entry.id && entry.diff && (
                  <div className="mt-3 pt-3 border-t border-charlie-border">
                    <pre className="terminal-block terminal-content text-xs overflow-x-auto">
                      {entry.diff}
                    </pre>
                  </div>
                )}

                {entry.diff && (
                  <div className="flex items-center gap-2 mt-3 pt-3 border-t border-charlie-border">
                    <Button
                      variant="ghost"
                      onClick={() => setExpandedId(expandedId === entry.id ? null : entry.id)}
                    >
                      {expandedId === entry.id ? 'Hide' : 'Show'} Diff
                    </Button>
                  </div>
                )}
              </GlassCard>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={<History size={32} />}
          title="No evolution history"
          description="Self-evolution improvements will appear here when generated by the EvolutionEngine"
          terminal
        />
      )}
    </div>
  )
}
