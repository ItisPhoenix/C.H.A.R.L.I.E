'use client'

import { useEffect, useState, useCallback } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Button } from '@/components/ui/Button'
import { StatusDot } from '@/components/ui/StatusDot'
import { PageHeader } from '@/components/layout/PageHeader'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { HudCorners } from '@/components/background/HudCorners'
import * as api from '@/lib/api'
import { cn } from '@/lib/utils'

interface GlobeStatus {
  running: boolean
  port: number
  layers?: Record<string, number>
}

const GLOBE_URL = 'http://localhost:8089'

export default function GlobePage() {
  const [status, setStatus] = useState<GlobeStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [launching, setLaunching] = useState(false)

  const loadStatus = useCallback(async () => {
    try {
      const data = await api.fetchGlobeStatus()
      setStatus(data as GlobeStatus)
    } catch {
      // keep existing state
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStatus()
    const interval = setInterval(loadStatus, 5000)
    return () => clearInterval(interval)
  }, [loadStatus])

  async function handleLaunch() {
    setLaunching(true)
    try {
      await api.launchGlobe()
      // Wait a moment for the server to start, then refresh
      setTimeout(async () => {
        await loadStatus()
        setLaunching(false)
      }, 2000)
    } catch {
      setLaunching(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <LoadingSpinner label="Loading globe status..." />
      </div>
    )
  }

  const isRunning = status?.running ?? false

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <PageHeader
        title="Globe"
        subtitle="3D World Map Visualization"
        actions={
          !isRunning ? (
            <Button
              variant="primary"
              loading={launching}
              onClick={handleLaunch}
            >
              Launch Globe
            </Button>
          ) : undefined
        }
      />

      {/* Status card */}
      <HudCorners>
      <GlassCard>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <StatusDot status={isRunning ? 'online' : 'idle'} pulse={isRunning} />
            <div>
              <span className="font-semibold text-sm text-charlie-text">
                Globe Server
              </span>
              <div className="text-xs text-charlie-dim mt-0.5">
                {isRunning
                  ? `Running on port ${status?.port ?? 8089}`
                  : 'Stopped'}
              </div>
            </div>
          </div>

          {isRunning && (
            <a
              href={GLOBE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-charlie-cyan hover:underline"
            >
              Open in new tab
            </a>
          )}
        </div>

        {/* Data layer summary */}
        {status?.layers && Object.keys(status.layers).length > 0 && (
          <div className="mt-4 pt-3 border-t border-charlie-border/30">
            <span className="text-xs text-charlie-dim font-medium uppercase mb-2 block">
              Data Layers
            </span>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {Object.entries(status.layers).map(([name, count]) => (
                <div
                  key={name}
                  className="flex items-center justify-between p-2 rounded bg-charlie-dark/40 border border-charlie-border/30"
                >
                  <span className="text-xs text-charlie-text capitalize">{name}</span>
                  <span className="text-xs font-mono text-charlie-cyan">{count}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </GlassCard>
      </HudCorners>

      {/* Globe embed */}
      {isRunning && (
        <GlassCard className="!p-0 overflow-hidden">
          <iframe
            src={GLOBE_URL}
            className="w-full border-0"
            style={{ height: '70vh', minHeight: '500px' }}
            title="CHARLIE Globe"
            allow="accelerometer; gyroscope"
          />
        </GlassCard>
      )}

      {!isRunning && (
        <GlassCard className="!p-8">
          <div className="flex flex-col items-center justify-center text-center">
            <div className="w-16 h-16 rounded-full bg-charlie-cyan/5 border border-charlie-cyan/20 flex items-center justify-center mb-4">
              <svg className="w-8 h-8 text-charlie-cyan/40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h3 className="text-charlie-text font-medium mb-1">Globe is not running</h3>
            <p className="text-charlie-dim text-sm max-w-md">
              Launch the Globe server to view the 3D world map with news, earthquakes, weather, and more.
            </p>
          </div>
        </GlassCard>
      )}
    </div>
  )
}
