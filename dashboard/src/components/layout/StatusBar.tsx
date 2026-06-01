'use client'

import { useState } from 'react'
import { useDashboardStore } from '@/lib/store'
import { formatUptime } from '@/lib/utils'
import { ConnectionBadge } from './ConnectionBadge'
import { Mic, MicOff, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

function MiniWaveform({ active, color }: { active: boolean; color: string }) {
  if (!active) return null
  return (
    <div className="flex items-end gap-px h-3">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="w-[2px] rounded-full transition-all duration-150"
          style={{
            backgroundColor: color,
            height: active ? `${6 + Math.random() * 6}px` : '2px',
            animation: active ? `waveform 0.8s ease-in-out ${i * 0.1}s infinite` : 'none',
          }}
        />
      ))}
    </div>
  )
}

export function StatusBar() {
  const [expanded, setExpanded] = useState(false)
  const daemon = useDashboardStore((s) => s.daemonStatus)
  const phase = useDashboardStore((s) => s.currentPhase)
  const voice = useDashboardStore((s) => s.voiceActivity)

  const isListening = voice?.is_listening ?? false
  const isSpeaking = voice?.is_speaking ?? false
  const isProcessing = phase === 'processing'
  const isVoiceActive = isListening || isSpeaking || isProcessing

  let voiceColorVar = 'var(--voice-idle)'
  let voiceLabel = 'Idle'
  if (isSpeaking) { voiceColorVar = 'var(--voice-speaking)'; voiceLabel = 'Speaking' }
  else if (isProcessing) { voiceColorVar = 'var(--voice-processing)'; voiceLabel = 'Processing' }
  else if (isListening) { voiceColorVar = 'var(--voice-listening)'; voiceLabel = 'Listening' }

  // Collapsed: just a small pill with connection + voice status
  if (!expanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        aria-label="Expand status bar"
        className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-mono premium-card cursor-pointer hover:scale-105 transition-transform"
      >
        <ConnectionBadge />
        <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: voiceColorVar }} />
        <span className="text-charlie-dim">{phase}</span>
        <ChevronDown size={12} className="text-charlie-dim" />
      </button>
    )
  }

  return (
    <div
      aria-label="Status bar"
      className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 flex items-center flex-wrap justify-center gap-3 sm:gap-6 px-3 sm:px-6 py-2.5 rounded-xl text-xs font-mono premium-card"
    >
      <ConnectionBadge />

      {/* Voice State Indicator */}
      <div className="flex items-center gap-2 px-3 py-1 rounded-md transition-all duration-200" style={{ background: `color-mix(in srgb, ${voiceColorVar} 8%, transparent)`, border: `1px solid color-mix(in srgb, ${voiceColorVar} 19%, transparent)` }}>
        {isVoiceActive ? (
          <Mic size={12} style={{ color: voiceColorVar }} />
        ) : (
          <MicOff size={12} className="text-charlie-dim" />
        )}
        <span className="font-semibold" style={{ color: voiceColorVar }}>{voiceLabel}</span>
        <MiniWaveform active={isVoiceActive} color={voiceColorVar} />
      </div>

      {daemon && (
        <>
          <MetricItem label="Uptime" value={formatUptime(daemon.uptime_seconds)} className="hidden sm:flex" />
          <MetricItem label="CPU" value={`${daemon.system.cpu}%`} warn={daemon.system.cpu > 70} />
          <MetricItem label="RAM" value={`${daemon.system.ram}%`} warn={daemon.system.ram > 80} className="hidden sm:flex" />
        </>
      )}

      <div className="flex items-center gap-1.5">
        <span className="text-charlie-dim">Phase</span>
        <span className="text-charlie-cyan capitalize font-semibold">{phase}</span>
      </div>

      <button
        onClick={() => setExpanded(false)}
        aria-label="Collapse status bar"
        className="text-charlie-dim hover:text-charlie-text cursor-pointer transition-colors"
      >
        <ChevronDown size={12} className="rotate-180" />
      </button>
    </div>
  )
}

function MetricItem({ label, value, warn, className }: { label: string; value: string; warn?: boolean; className?: string }) {
  return (
    <div className={`flex items-center gap-1.5 ${className ?? ''}`}>
      <span className="text-charlie-dim">{label}</span>
      <span className={warn ? 'text-charlie-amber font-semibold' : 'text-charlie-text'}>
        {value}
      </span>
    </div>
  )
}
