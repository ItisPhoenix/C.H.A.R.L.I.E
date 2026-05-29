'use client'

import { useEffect, useState } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { PageHeader } from '@/components/layout/PageHeader'
import { fetchSettings, shutdownDaemon, rebootDaemon } from '@/lib/api'
import { useRouter } from 'next/navigation'
import { cn } from '@/lib/utils'
import type { CharlieSettings } from '@/lib/types'

type SectionKey = 'llm' | 'audio' | 'security' | 'startup' | 'persona'

const SECTION_LABELS: Record<SectionKey, string> = {
  llm: 'LLM Configuration',
  audio: 'Audio & Voice',
  security: 'Security & Safety',
  startup: 'Startup Behavior',
  persona: 'Persona',
}

const FIELD_LABELS: Record<string, Record<string, string>> = {
  llm: {
    llm_url: 'LLM Endpoint',
    primary_model: 'Primary Model',
    vision_model: 'Vision Model',
    context_window: 'Context Window',
    temperature: 'Temperature',
  },
  audio: {
    stt_model: 'STT Model',
    pocket_tts_model: 'TTS Model',
    pocket_tts_speed: 'TTS Speed',
    mic_index: 'Microphone Index',
    output_index: 'Output Index',
  },
  security: {
    tier_2_countdown: 'Tier 2 Countdown (s)',
    snapshots_enabled: 'Snapshots',
    require_confirmation_tier1: 'Confirm Tier 1',
  },
  startup: {
    run_news_sweep: 'News Sweep on Boot',
    play_music: 'Play Music on Boot',
    speak_welcome: 'Speak Welcome',
  },
  persona: {
    address_user_as: 'Address User As',
    response_style: 'Response Style',
    verbosity: 'Verbosity',
  },
}

const SECTION_ICONS: Record<SectionKey, string> = {
  llm: 'M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z',
  audio: 'M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z',
  security: 'M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z',
  startup: 'M13 10V3L4 14h7v7l9-11h-7z',
  persona: 'M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z',
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<CharlieSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Set<SectionKey>>(new Set())
  const [confirmAction, setConfirmAction] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState(false)

  useEffect(() => {
    loadSettings()
  }, [])

  async function loadSettings() {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchSettings()
      setSettings(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load settings')
    }
    setLoading(false)
  }

  function toggleSection(key: SectionKey) {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  async function executeAction(action: () => Promise<unknown>) {
    setActionLoading(true)
    try {
      await action()
    } catch {
      // action failed
    }
    setActionLoading(false)
    setConfirmAction(null)
  }

  const router = useRouter()

  const daemonActions: Array<{
    label: string
    action: string
    handler: () => Promise<unknown>
    variant: 'primary' | 'danger' | 'ghost'
  }> = [
    { label: 'Run Setup Wizard', action: 'setup', handler: async () => router.push('/setup'), variant: 'ghost' },
    { label: 'Reboot Daemon', action: 'reboot', handler: rebootDaemon, variant: 'primary' },
    { label: 'Shutdown Daemon', action: 'shutdown', handler: shutdownDaemon, variant: 'danger' },
  ]

  const confirmLabels: Record<string, string> = {
    setup: 'Run the setup wizard? This will reconfigure CHARLIE.',
    reboot: 'Reboot the daemon? This will restart all subsystems.',
    shutdown: 'Shutdown the daemon? All subsystems will stop.',
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <LoadingSpinner label="Loading settings..." />
      </div>
    )
  }

  if (error || !settings) {
    return (
      <div className="max-w-4xl mx-auto">
        <PageHeader title="Settings" />
        <GlassCard>
          <p className="text-charlie-red text-sm text-center py-4">
            {error || 'Failed to load settings'}
          </p>
          <div className="text-center mt-2">
            <Button variant="ghost" size="sm" onClick={loadSettings}>
              Retry
            </Button>
          </div>
        </GlassCard>
      </div>
    )
  }

  const sections: SectionKey[] = ['llm', 'audio', 'security', 'startup', 'persona']

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <PageHeader title="Settings" subtitle="Daemon configuration (read-only)" />

      {sections.map((sectionKey) => {
        const sectionData = settings[sectionKey] as Record<string, unknown> | undefined
        if (!sectionData) return null
        const labels = FIELD_LABELS[sectionKey] || {}
        const isOpen = !collapsed.has(sectionKey)

        return (
          <GlassCard key={sectionKey} className="!p-0">
            <button
              onClick={() => toggleSection(sectionKey)}
              className="w-full flex items-center justify-between p-4 cursor-pointer"
            >
              <div className="flex items-center gap-3">
                <svg className="h-4 w-4 text-charlie-cyan" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={SECTION_ICONS[sectionKey]} />
                </svg>
                <h2 className="text-sm font-semibold text-charlie-cyan font-display tracking-[0.1em] uppercase">
                  {SECTION_LABELS[sectionKey]}
                </h2>
              </div>
              <svg
                className={cn('h-4 w-4 text-charlie-dim transition-transform', isOpen && 'rotate-180')}
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

            {isOpen && (
              <div className="border-t border-charlie-border px-4 py-3 space-y-3">
                {Object.entries(sectionData).map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between">
                    <span className="text-sm text-charlie-dim">
                      {labels[key] || key}
                    </span>
                    <span className="text-sm text-charlie-text font-mono bg-charlie-card border border-charlie-border rounded px-3 py-1 max-w-[260px] truncate text-right shadow-neon-cyan-sm">
                      {formatValue(value)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </GlassCard>
        )
      })}

      {/* Daemon Control */}
      <GlassCard>
        <h2 className="text-sm font-semibold text-charlie-cyan mb-4 font-display tracking-[0.1em] uppercase">Daemon Control</h2>
        <div className="flex flex-wrap gap-3">
          {daemonActions.map((da) => (
            <Button
              key={da.action}
              variant={da.variant}
              size="md"
              onClick={() => setConfirmAction(da.action)}
            >
              {da.label}
            </Button>
          ))}
        </div>
      </GlassCard>

      {/* Confirmation Modal */}
      <Modal
        open={confirmAction !== null}
        onClose={() => setConfirmAction(null)}
        title="Confirm Action"
      >
        <p className="text-sm text-charlie-text mb-6">
          {confirmAction ? confirmLabels[confirmAction] : ''}
        </p>
        <div className="flex justify-end gap-3">
          <Button variant="ghost" size="sm" onClick={() => setConfirmAction(null)}>
            Cancel
          </Button>
          <Button
            variant={confirmAction === 'shutdown' ? 'danger' : 'primary'}
            size="sm"
            loading={actionLoading}
            onClick={() => {
              const da = daemonActions.find((d) => d.action === confirmAction)
              if (da) executeAction(da.handler)
            }}
          >
            Confirm
          </Button>
        </div>
      </Modal>
    </div>
  )
}

function formatValue(value: unknown): string {
  if (typeof value === 'boolean') return value ? 'Enabled' : 'Disabled'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') return value
  return String(value)
}
