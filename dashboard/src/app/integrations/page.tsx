'use client'

import { useEffect, useState, useCallback } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { StatusDot } from '@/components/ui/StatusDot'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorState } from '@/components/ui/ErrorState'
import { PageHeader } from '@/components/layout/PageHeader'
import { fetchIntegrations } from '@/lib/api'
import { formatTimestamp, createVisibilityAwareInterval } from '@/lib/utils'
import { FilterBar } from '@/components/ui/FilterBar'
import { useWSEvent } from '@/lib/ws'
import type { IntegrationHealth } from '@/lib/types'

type StatusFilter = 'all' | 'connected' | 'error'

const STATUS_BADGE_VARIANT: Record<string, 'green' | 'dim' | 'red' | 'amber'> = {
  connected: 'green',
  disconnected: 'dim',
  error: 'red',
  auth_expired: 'amber',
}

const STATUS_DOT_STATUS: Record<string, 'online' | 'error' | 'idle' | 'warning'> = {
  connected: 'online',
  disconnected: 'idle',
  error: 'error',
  auth_expired: 'warning',
}

export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<IntegrationHealth[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')

  const wsIntegration = useWSEvent<Record<string, unknown>>('INTEGRATION_UPDATE')

  const loadIntegrations = useCallback(async () => {
    try {
      const data = await fetchIntegrations()
      setIntegrations(data.integrations || [])
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch integrations')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadIntegrations()
    return createVisibilityAwareInterval(loadIntegrations, 5000)
  }, [loadIntegrations])

  useEffect(() => {
    if (wsIntegration) {
      setIntegrations(prev => prev.map(int =>
        int.name === (wsIntegration.name as string) ? { ...int, ...wsIntegration } : int
      ))
    }
  }, [wsIntegration])

  const filtered = integrations.filter((int) => {
    if (statusFilter === 'all') return true
    if (statusFilter === 'connected') return int.status === 'connected'
    return int.status === 'error' || int.status === 'auth_expired'
  })

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto space-y-6">
        <PageHeader title="Integrations" subtitle="Loading..." />
        <div className="flex items-center justify-center h-[60vh]">
          <LoadingSpinner size="lg" label="Fetching integrations..." />
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="max-w-6xl mx-auto space-y-6">
        <PageHeader title="Integrations" />
        <GlassCard>
          <ErrorState error={error} onRetry={loadIntegrations} />
        </GlassCard>
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <PageHeader
        title="Integrations"
        subtitle={`${integrations.length} registered`}
      />

      {/* Status filter tabs */}
      <FilterBar
        options={['all', 'connected', 'error'] as const}
        value={statusFilter}
        onChange={setStatusFilter}
        badge={(key) =>
          key === 'all'
            ? integrations.length
            : integrations.filter((i) =>
                key === 'connected'
                  ? i.status === 'connected'
                  : i.status === 'error' || i.status === 'auth_expired'
              ).length
        }
      />

      {filtered.length === 0 ? (
        <GlassCard>
          <EmptyState
            title="No integrations found"
            description={statusFilter !== 'all' ? 'No integrations match this filter.' : 'Integrations will appear here once configured.'}
          />
        </GlassCard>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((integration) => (
            <IntegrationCard key={integration.name} integration={integration} />
          ))}
        </div>
      )}
    </div>
  )
}

function IntegrationCard({ integration }: { integration: IntegrationHealth }) {
  const statusVariant = STATUS_BADGE_VARIANT[integration.status] || 'dim'
  const dotStatus = STATUS_DOT_STATUS[integration.status] || 'idle'

  return (
      <GlassCard className="hover:shadow-neon-cyan-sm transition-all">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <StatusDot status={dotStatus} pulse={integration.status === 'connected'} />
            <span className="font-display text-sm text-charlie-text tracking-wide">{integration.name}</span>
          </div>
          <Badge variant={statusVariant}>{integration.status}</Badge>
        </div>

        <div className="space-y-2 text-xs font-body">
          <div className="flex justify-between">
            <span className="text-charlie-dim">Auth</span>
            <span className="text-charlie-text capitalize">{integration.auth_method}</span>
          </div>

          {integration.capabilities.length > 0 && (
            <div>
              <span className="text-charlie-dim block mb-1">Capabilities</span>
              <div className="flex flex-wrap gap-1">
                {integration.capabilities.map((cap) => (
                  <span
                    key={cap}
                    className="px-1.5 py-0.5 text-xs rounded bg-charlie-cyan/5 text-charlie-dim border border-charlie-border font-mono"
                  >
                    {cap}
                  </span>
                ))}
              </div>
            </div>
          )}

          {integration.last_sync && (
            <div className="flex justify-between">
              <span className="text-charlie-dim">Last Sync</span>
              <span className="text-charlie-text font-mono">{formatTimestamp(integration.last_sync)}</span>
            </div>
          )}
        </div>

        {integration.last_error && (
          <div className="mt-3 pt-2 border-t border-charlie-border">
            <p className="text-xs text-charlie-red font-body">{integration.last_error}</p>
          </div>
        )}
      </GlassCard>
  )
}
