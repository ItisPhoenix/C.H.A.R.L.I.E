'use client'

import { useDashboardStore } from '@/lib/store'
import { formatUptime } from '@/lib/utils'
import { ConnectionBadge } from './ConnectionBadge'
import { Mic, MicOff } from 'lucide-react'
import { cn } from '@/lib/utils'

// Mini waveform bars for status bar
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
  const daemon = useDashboardStore((s) => s.daemonStatus)
  const phase = useDashboardStore((s) => s.currentPhase)
  const voice = useDashboardStore((s) => s.voiceActivity)

  const isListening = voice?.is_listening ?? false
  const isSpeaking = voice?.is_speaking ?? false
  const isProcessing = phase === 'processing'
  const isVoiceActive = isListening || isSpeaking || isProcessing

  let voiceColor = '#64748B'
  let voiceLabel = 'Idle'
  if (isSpeaking) { voiceColor = '#22C55E'; voiceLabel = 'Speaking' }
  else if (isProcessing) { voiceColor = '#F59E0B'; voiceLabel = 'Processing' }
  else if (isListening) { voiceColor = '#00D4FF'; voiceLabel = 'Listening' }

  return (
    <div
      className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 flex items-center gap-5 px-5 py-2 rounded-2xl text-xs font-mono glass-card"
    >
      <ConnectionBadge />

      {/* Voice State Indicator */}
      <div className="flex items-center gap-2 px-2 py-0.5 rounded-lg" style={{ background: `${voiceColor}10`, border: `1px solid ${voiceColor}30` }}>
        {isVoiceActive ? (
          <Mic size={12} style={{ color: voiceColor }} />
        ) : (
          <MicOff size={12} className="text-charlie-dim" />
        )}
        <span className="font-semibold" style={{ color: voiceColor }}>{voiceLabel}</span>
        <MiniWaveform active={isVoiceActive} color={voiceColor} />
      </div>

      {daemon && (
        <>
          <MetricItem label="Uptime" value={formatUptime(daemon.uptime_seconds)} />
          <MetricItem label="CPU" value={`${daemon.system.cpu}%`} warn={daemon.system.cpu > 70} />
          <MetricItem label="RAM" value={`${daemon.system.ram}%`} warn={daemon.system.ram > 80} />
        </>
      )}

      <div className="flex items-center gap-1.5">
        <span className="text-charlie-dim">Phase</span>
        <span className="text-charlie-cyan capitalize font-semibold">{phase}</span>
      </div>
    </div>
  )
}

function MetricItem({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-charlie-dim">{label}</span>
      <span className={warn ? 'text-charlie-amber font-semibold' : 'text-charlie-text'}>
        {value}
      </span>
    </div>
  )
}
