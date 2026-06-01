'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Button } from '@/components/ui/Button'
import { Toggle } from '@/components/ui/Toggle'
import { Modal } from '@/components/ui/Modal'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorState } from '@/components/ui/ErrorState'
import { PageHeader } from '@/components/layout/PageHeader'
import { fetchSettings, saveSettings as saveSettingsApi, shutdownDaemon, rebootDaemon } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Cpu, Volume2, Shield, Zap, User, ChevronDown, Save, Eye, EyeOff, X } from 'lucide-react'
import type { CharlieSettings } from '@/lib/types'

type SectionKey = 'llm' | 'audio' | 'security' | 'resources' | 'persona'

const SECTION_LABELS: Record<SectionKey, string> = {
  llm: 'LLM Configuration',
  audio: 'Audio & Voice',
  security: 'Security & Safety',
  resources: 'Resources',
  persona: 'Persona',
}

const FIELD_LABELS: Record<string, Record<string, string>> = {
  llm: {
    provider: 'Provider',
    llm_url: 'LLM Endpoint',
    nim_api_key: 'API Key',
    primary_model: 'Primary Model',
    vision_model: 'Vision Model',
    vision_url: 'Vision Endpoint',
    embedding_url: 'Embedding Endpoint',
    embedding_model: 'Embedding Model',
    context_window: 'Context Window',
    temperature: 'Temperature',
  },
  audio: {
    stt_model: 'STT Model',
    pocket_tts_model: 'TTS Model',
    pocket_tts_speed: 'TTS Speed',
    mic_index: 'Microphone Index',
    output_index: 'Output Index',
    voice_mode: 'Voice Mode',
  },
  security: {
    tier_2_countdown: 'Tier 2 Countdown (s)',
    snapshots_enabled: 'Snapshots',
    require_confirmation_tier1: 'Confirm Tier 1',
    self_modify_enabled: 'Self-Modify',
    auto_patcher_enabled: 'Auto-Patcher',
    restricted_paths: 'Restricted Paths',
  },
  resources: {
    vram_budget_mb: 'VRAM Budget (MB)',
  },
  persona: {
    address_user_as: 'Address User As',
    response_style: 'Response Style',
    verbosity: 'Verbosity',
  },
}

const SECTION_ICONS: Record<SectionKey, React.ComponentType<Record<string, unknown>>> = {
  llm: Cpu as React.ComponentType<Record<string, unknown>>,
  audio: Volume2 as React.ComponentType<Record<string, unknown>>,
  security: Shield as React.ComponentType<Record<string, unknown>>,
  resources: Zap as React.ComponentType<Record<string, unknown>>,
  persona: User as React.ComponentType<Record<string, unknown>>,
}

const PROVIDER_ENDPOINTS: Record<string, string> = {
  'LM Studio': 'http://localhost:1234',
  'NVIDIA NIM': 'https://integrate.api.nvidia.com',
  'OpenRouter': 'https://openrouter.ai/api/v1',
  'Gemini': 'https://generativelanguage.googleapis.com/v1beta',
  'Groq': 'https://api.groq.com/openai/v1',
}

function isSensitiveField(key: string): boolean {
  return /api_key|token|secret|password/i.test(key)
}

function detectProvider(url: string): string {
  for (const [provider, endpoint] of Object.entries(PROVIDER_ENDPOINTS)) {
    if (url === endpoint) return provider
  }
  return ''
}

/** Tag/chip input for string[] fields (e.g. restricted_paths) */
function TagInput({
  value,
  onChange,
  placeholder,
}: {
  value: string[]
  onChange: (v: string[]) => void
  placeholder?: string
}) {
  const [input, setInput] = useState('')

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && input.trim()) {
      e.preventDefault()
      const trimmed = input.trim()
      if (!value.includes(trimmed)) {
        onChange([...value, trimmed])
      }
      setInput('')
    }
    if (e.key === 'Backspace' && input === '' && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }

  function removeTag(tag: string) {
    onChange(value.filter((t) => t !== tag))
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5 bg-charlie-card border border-charlie-border rounded-lg px-2.5 py-1.5 w-full max-w-[260px] min-h-[36px] focus-within:border-charlie-cyan transition-colors">
      {value.map((tag) => (
        <span
          key={tag}
          className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-md bg-charlie-cyan/10 text-charlie-cyan/80"
        >
          {tag}
          <button
            type="button"
            onClick={() => removeTag(tag)}
            className="hover:text-charlie-cyan transition-colors"
            aria-label={`Remove ${tag}`}
          >
            <X size={12} />
          </button>
        </span>
      ))}
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={value.length === 0 ? placeholder || 'Add...' : ''}
        className="flex-1 min-w-[60px] text-sm text-charlie-text bg-transparent border-none outline-none placeholder:text-charlie-dim/50"
      />
    </div>
  )
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<CharlieSettings | null>(null)
  const [original, setOriginal] = useState<CharlieSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Set<SectionKey>>(new Set())
  const [confirmAction, setConfirmAction] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [selectedProvider, setSelectedProvider] = useState<string>('')
  const [visibleSecrets, setVisibleSecrets] = useState<Set<string>>(new Set())
  const userCustomizedUrl = useRef(false)
  const initialDetectionDone = useRef(false)

  const loadSettings = useCallback(async () => {
    setLoading(true)
    setError(null)
    initialDetectionDone.current = false
    userCustomizedUrl.current = false
    try {
      const data = await fetchSettings()
      setSettings(data)
      setOriginal(JSON.parse(JSON.stringify(data)))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load settings')
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    loadSettings()
  }, [loadSettings])

  // Auto-detect provider from current llm_url on initial load
  useEffect(() => {
    if (settings?.llm?.llm_url && !initialDetectionDone.current) {
      const detected = detectProvider(settings.llm.llm_url as string)
      if (detected) setSelectedProvider(detected)
      initialDetectionDone.current = true
    }
  }, [settings?.llm?.llm_url])

  function toggleSection(key: SectionKey) {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function updateField(section: string, key: string, value: unknown) {
    if (!settings) return
    setSettings({
      ...settings,
      [section]: {
        ...(settings[section as keyof CharlieSettings] as Record<string, unknown> || {}),
        [key]: value,
      },
    } as CharlieSettings)
  }

  function handleFieldChange(section: string, key: string, value: unknown) {
    if (section === 'llm' && key === 'llm_url') {
      userCustomizedUrl.current = true
    }
    updateField(section, key, value)
  }

  function handleProviderChange(newProvider: string) {
    setSelectedProvider(newProvider)
    // Auto-fill endpoint only if user hasn't manually customized the URL
    if (PROVIDER_ENDPOINTS[newProvider] && !userCustomizedUrl.current) {
      updateField('llm', 'llm_url', PROVIDER_ENDPOINTS[newProvider])
    }
    // Reset the customization flag when explicitly picking a provider
    userCustomizedUrl.current = false
  }

  function toggleSecretVisibility(fieldId: string) {
    setVisibleSecrets((prev) => {
      const next = new Set(prev)
      if (next.has(fieldId)) next.delete(fieldId)
      else next.add(fieldId)
      return next
    })
  }

  function isDirty(): boolean {
    if (!settings || !original) return false
    return JSON.stringify(settings) !== JSON.stringify(original)
  }

  async function saveSettings() {
    if (!settings || !original) return
    setSaving(true)
    try {
      // Build diff: only send changed sections
      const diff: Record<string, Record<string, unknown>> = {}
      for (const [section, values] of Object.entries(settings)) {
        if (typeof values !== 'object' || values === null) continue
        const origValues = (original as Record<string, unknown>)[section] as Record<string, unknown> || {}
        const changed: Record<string, unknown> = {}
        for (const [k, v] of Object.entries(values as Record<string, unknown>)) {
          if (JSON.stringify(v) !== JSON.stringify(origValues[k])) {
            changed[k] = v
          }
        }
        if (Object.keys(changed).length > 0) diff[section] = changed
      }
      if (Object.keys(diff).length === 0) {
        setSaving(false)
        return
      }
      const data = await saveSettingsApi(diff)
      if (data.ok) {
        setOriginal(JSON.parse(JSON.stringify(settings)))
        setSaved(true)
        setTimeout(() => setSaved(false), 3000)
      } else {
        setError(data.error || 'Failed to save settings')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save settings')
    }
    setSaving(false)
  }

  async function executeAction(action: () => Promise<unknown>) {
    setActionLoading(true)
    try {
      await action()
    } catch (e) {
      console.error('Failed to execute daemon action:', e)
    }
    setActionLoading(false)
    setConfirmAction(null)
  }

  const daemonActions = [
    { label: 'Reboot Daemon', action: 'reboot', handler: rebootDaemon, variant: 'primary' as const },
    { label: 'Shutdown Daemon', action: 'shutdown', handler: shutdownDaemon, variant: 'danger' as const },
  ]

  const confirmLabels: Record<string, string> = {
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
        <ErrorState error={error || 'Failed to load settings'} onRetry={loadSettings} />
      </div>
    )
  }

  const sections: SectionKey[] = ['llm', 'audio', 'security', 'resources', 'persona']

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <PageHeader
        title="Settings"
        subtitle={isDirty() ? 'Unsaved changes' : 'Daemon configuration'}
        actions={
          <div className="flex items-center gap-3">
            {saved && (
              <span className="text-sm text-charlie-green animate-pulse">Saved</span>
            )}
            <Button
              variant="primary"
              size="sm"
              loading={saving}
              disabled={!isDirty()}
              onClick={saveSettings}
            >
              <Save size={14} className="mr-1" />
              Save Changes
            </Button>
          </div>
        }
      />

      {sections.map((sectionKey) => {
        const sectionData = settings[sectionKey] as Record<string, unknown> | undefined
        if (!sectionData) return null
        const labels = FIELD_LABELS[sectionKey] || {}
        const isOpen = !collapsed.has(sectionKey)

        return (
          <GlassCard key={sectionKey} className="!p-0">
            <Button
              variant="ghost"
              onClick={() => toggleSection(sectionKey)}
              className="w-full flex items-center justify-between !p-4 !rounded-none"
            >
              <div className="flex items-center gap-3">
                {(() => {
                  const IconComponent = SECTION_ICONS[sectionKey]
                  return <IconComponent size={16} className="text-charlie-cyan" />
                })()}
                <h2 className="text-sm font-semibold text-charlie-cyan font-display tracking-[0.1em] uppercase">
                  {SECTION_LABELS[sectionKey]}
                </h2>
              </div>
              <ChevronDown size={16} className={cn('text-charlie-dim transition-transform', isOpen && 'rotate-180')} />
            </Button>

            {isOpen && (
              <div className="border-t border-charlie-border px-4 py-3 space-y-4">
                {/* Provider dropdown — rendered first in LLM section */}
                {sectionKey === 'llm' && (
                  <div className="grid grid-cols-[1fr_auto] gap-4 items-center">
                    <span className="text-sm text-charlie-dim truncate" title="Provider">
                      Provider
                    </span>
                    <select
                      value={selectedProvider}
                      onChange={(e) => handleProviderChange(e.target.value)}
                      className="text-sm text-charlie-text font-mono bg-charlie-card border border-charlie-border rounded-lg px-3 py-1.5 w-full max-w-[260px] focus:border-charlie-cyan focus:outline-none transition-colors cursor-pointer"
                    >
                      <option value="">Select provider...</option>
                      {Object.keys(PROVIDER_ENDPOINTS).map((p) => (
                        <option key={p} value={p}>{p}</option>
                      ))}
                    </select>
                  </div>
                )}

                {Object.entries(sectionData).map(([key, value]) => {
                  const fieldId = `${sectionKey}.${key}`
                  const sensitive = isSensitiveField(key)

                  return (
                    <div key={key} className="grid grid-cols-[1fr_auto] gap-4 items-center">
                      <span className="text-sm text-charlie-dim truncate" title={labels[key] || key}>
                        {labels[key] || key}
                      </span>
                      {renderEditableField(
                        sectionKey,
                        key,
                        value,
                        (v) => handleFieldChange(sectionKey, key, v),
                        sensitive
                          ? {
                              isVisible: visibleSecrets.has(fieldId),
                              onToggleVisibility: () => toggleSecretVisibility(fieldId),
                            }
                          : undefined,
                      )}
                    </div>
                  )
                })}
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

function renderEditableField(
  section: string,
  key: string,
  value: unknown,
  onChange: (v: unknown) => void,
  secretToggle?: { isVisible: boolean; onToggleVisibility: () => void },
) {
  // Array fields -> Tag input
  if (Array.isArray(value)) {
    return (
      <TagInput
        value={value as string[]}
        onChange={onChange as (v: string[]) => void}
        placeholder="Add path..."
      />
    )
  }

  // Boolean fields -> Toggle
  if (typeof value === 'boolean') {
    return <Toggle enabled={value} onChange={onChange} />
  }

  // Number fields -> number input
  if (typeof value === 'number') {
    return (
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="text-sm text-charlie-text font-mono bg-charlie-card border border-charlie-border rounded-lg px-3 py-1.5 w-full max-w-[260px] text-right focus:border-charlie-cyan focus:outline-none transition-colors"
      />
    )
  }

  // String fields -> text input (with password masking for sensitive fields)
  if (typeof value === 'string') {
    const sensitive = isSensitiveField(key)

    if (sensitive && secretToggle) {
      return (
        <div className="relative w-full max-w-[260px]">
          <input
            type={secretToggle.isVisible ? 'text' : 'password'}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="text-sm text-charlie-text font-mono bg-charlie-card border border-charlie-border rounded-lg px-3 py-1.5 pr-9 w-full text-right focus:border-charlie-cyan focus:outline-none transition-colors"
          />
          <button
            type="button"
            onClick={secretToggle.onToggleVisibility}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-charlie-dim hover:text-charlie-cyan transition-colors"
            aria-label={secretToggle.isVisible ? 'Hide value' : 'Show value'}
          >
            {secretToggle.isVisible ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>
      )
    }

    return (
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="text-sm text-charlie-text font-mono bg-charlie-card border border-charlie-border rounded-lg px-3 py-1.5 w-full max-w-[260px] text-right focus:border-charlie-cyan focus:outline-none transition-colors"
      />
    )
  }

  // Fallback: read-only
  return (
    <span className="text-sm text-charlie-text font-mono bg-charlie-card border border-charlie-border rounded-lg px-3 py-1 max-w-[260px] truncate text-right">
      {String(value)}
    </span>
  )
}
