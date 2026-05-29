'use client'

import { useEffect, useState, useCallback } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { StatusDot } from '@/components/ui/StatusDot'
import { Metric } from '@/components/ui/Metric'
import { Modal } from '@/components/ui/Modal'
import { ErrorState } from '@/components/ui/ErrorState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { HudCorners } from '@/components/background/HudCorners'
import { ResourceBar } from '@/components/charts/ResourceBar'
import { PageHeader } from '@/components/layout/PageHeader'
import { useWebSocket } from '@/lib/ws'
import {
  fetchStatus,
  restartSubsystem,
  stopSubsystem,
  shutdownDaemon,
  rebootDaemon,
} from '@/lib/api'
import { formatUptime, formatBytes, cn } from '@/lib/utils'
import type { DaemonStatus, SubsystemStatus } from '@/lib/types'

const SUBSYSTEM_ORDER = ['audio', 'brain', 'browser', 'telegram', 'vision'] as const

function subsystemDotStatus(status: string): 'online' | 'error' | 'idle' {
  if (status === 'running') return 'online'
  if (status === 'stopped') return 'idle'
  return 'error'
}

export default function StatusPage() {
  const [status, setStatus] = useState<DaemonStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [shutdownModal, setShutdownModal] = useState(false)
  const [rebootModal, setRebootModal] = useState(false)

  const { subscribe } = useWebSocket()

  const loadStatus = useCallback(async () => {
    try {
      const data = await fetchStatus()
      setStatus(data)
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch status')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStatus()
    const interval = setInterval(loadStatus, 5000)
    return () => clearInterval(interval)
  }, [loadStatus])

  useEffect(() => {
    const unsub = subscribe('status_update', () => {
      loadStatus()
    })
    return unsub
  }, [subscribe, loadStatus])

  async function handleSubsystemAction(name: string, action: 'restart' | 'stop') {
    setActionLoading(`${name}-${action}`)
    try {
      if (action === 'restart') await restartSubsystem(name)
      else await stopSubsystem(name)
      await loadStatus()
    } catch {
      // silently fail, next poll will refresh
    } finally {
      setActionLoading(null)
    }
  }

  async function handleShutdown() {
    setShutdownModal(false)
    setActionLoading('shutdown')
    try {
      await shutdownDaemon()
    } catch {
      // daemon will stop
    }
  }

  async function handleReboot() {
    setRebootModal(false)
    setActionLoading('reboot')
    try {
      await rebootDaemon()
    } catch {
      // daemon will restart
    } finally {
      setActionLoading(null)
    }
  }

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto space-y-6">
        <PageHeader title="System Status" subtitle="Loading..." />
        <div className="flex items-center justify-center h-64">
          <LoadingSpinner size="lg" label="Connecting to Charlie..." />
        </div>
      </div>
    )
  }

  if (error || !status) {
    return (
      <div className="max-w-6xl mx-auto space-y-6">
        <PageHeader title="System Status" />
        <GlassCard>
          <ErrorState error={error || 'No data received'} onRetry={loadStatus} />
        </GlassCard>
      </div>
    )
  }

  const subsystemEntries = Object.entries(status.subsystems).sort(([a], [b]) => {
    const ai = SUBSYSTEM_ORDER.indexOf(a.toLowerCase() as typeof SUBSYSTEM_ORDER[number])
    const bi = SUBSYSTEM_ORDER.indexOf(b.toLowerCase() as typeof SUBSYSTEM_ORDER[number])
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
  })

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <PageHeader
        title="System Status"
        subtitle={`Uptime: ${formatUptime(status.uptime_seconds)}`}
        actions={
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRebootModal(true)}
              disabled={actionLoading === 'reboot'}
            >
              Reboot
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={() => setShutdownModal(true)}
              disabled={actionLoading === 'shutdown'}
            >
              Shutdown
            </Button>
          </div>
        }
      />

      {/* System Resources */}
      <GlassCard glow>
        <div className="flex items-center gap-6 mb-4">
          <Metric label="Uptime" value={formatUptime(status.uptime_seconds)} />
          <Metric label="CPU" value={`${status.system.cpu}%`} />
          <Metric label="RAM" value={`${status.system.ram}%`} />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <ResourceBar label="System CPU" value={status.system.cpu} />
          <ResourceBar label="System RAM" value={status.system.ram} />
        </div>
      </GlassCard>

      {/* Subsystem Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {subsystemEntries.map(([name, sub]) => (
          <HudCorners key={name}>
            <SubsystemCard
              name={name}
              sub={sub}
              actionLoading={actionLoading}
              onRestart={() => handleSubsystemAction(name, 'restart')}
              onStop={() => handleSubsystemAction(name, 'stop')}
            />
          </HudCorners>
        ))}
      </div>

      {/* Quick Actions */}
      <GlassCard>
        <h3 className="text-sm font-display font-semibold text-charlie-cyan mb-4 tracking-wide">Quick Actions</h3>
        <div className="flex flex-wrap gap-3">
          <Button variant="danger" onClick={() => setShutdownModal(true)}>
            Shutdown Daemon
          </Button>
          <Button variant="ghost" onClick={() => setRebootModal(true)}>
            Reboot Daemon
          </Button>
        </div>
      </GlassCard>

      {/* Shutdown Modal */}
      <Modal open={shutdownModal} onClose={() => setShutdownModal(false)} title="Confirm Shutdown">
        <p className="text-charlie-dim text-sm mb-4">
          This will shut down the Charlie daemon and all subsystems. You will need to restart manually.
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={() => setShutdownModal(false)}>
            Cancel
          </Button>
          <Button variant="danger" size="sm" onClick={handleShutdown}>
            Shut Down
          </Button>
        </div>
      </Modal>

      {/* Reboot Modal */}
      <Modal open={rebootModal} onClose={() => setRebootModal(false)} title="Confirm Reboot">
        <p className="text-charlie-dim text-sm mb-4">
          This will restart the Charlie daemon and all subsystems. The dashboard may be briefly unavailable.
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={() => setRebootModal(false)}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" onClick={handleReboot}>
            Reboot
          </Button>
        </div>
      </Modal>
    </div>
  )
}

// --- SubsystemCard ---

interface SubsystemCardProps {
  name: string
  sub: SubsystemStatus
  actionLoading: string | null
  onRestart: () => void
  onStop: () => void
}

function SubsystemCard({ name, sub, actionLoading, onRestart, onStop }: SubsystemCardProps) {
  const isRunning = sub.status === 'running'
  const dotStatus = subsystemDotStatus(sub.status)

  return (
    <GlassCard>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <StatusDot status={dotStatus} pulse={isRunning} />
          <span className="font-display font-semibold text-sm capitalize tracking-wide">{name}</span>
        </div>
        <Badge variant={isRunning ? 'green' : 'dim'}>
          {sub.status}
        </Badge>
      </div>

      <div className="grid grid-cols-2 gap-y-2 gap-x-4 text-xs mb-3">
        <Metric label="PID" value={sub.pid > 0 ? String(sub.pid) : '--'} />
        <Metric label="CPU" value={`${sub.cpu.toFixed(1)}%`} />
        <Metric label="RAM" value={formatBytes(sub.ram_mb)} />
        <Metric label="Restarts" value={String(sub.restarts)} />
      </div>

      <div className="flex gap-2 pt-2 border-t border-charlie-border">
        <Button
          variant="ghost"
          size="sm"
          loading={actionLoading === `${name}-restart`}
          onClick={onRestart}
          className="flex-1"
        >
          Restart
        </Button>
        <Button
          variant="danger"
          size="sm"
          loading={actionLoading === `${name}-stop`}
          onClick={onStop}
          disabled={!isRunning}
          className="flex-1"
        >
          Stop
        </Button>
      </div>
    </GlassCard>
  )
}
