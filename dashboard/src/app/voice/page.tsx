'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { StatusDot } from '@/components/ui/StatusDot'
import { WaveformVisualizer } from '@/components/charts/WaveformVisualizer'
import { PageHeader } from '@/components/layout/PageHeader'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import * as api from '@/lib/api'
import { useWSEvent } from '@/lib/ws'
import { cn } from '@/lib/utils'
import type { VoiceActivity } from '@/lib/types'

interface VoiceStatusInfo {
  stt_model: string
  tts_model: string
  tts_speed: number
}

function getVoiceState(activity: VoiceActivity | null): {
  label: string
  status: 'online' | 'idle' | 'warning'
  active: boolean
} {
  if (!activity) return { label: 'Idle', status: 'idle', active: false }
  if (activity.is_speaking) return { label: 'Speaking', status: 'online', active: true }
  if (activity.is_listening) return { label: 'Listening', status: 'online', active: true }
  if (activity.wake_word_detected) return { label: 'Wake word detected', status: 'warning', active: true }
  return { label: 'Idle', status: 'idle', active: false }
}

interface TranscriptEntry {
  id: string
  content: string
  timestamp: Date
}

export default function VoicePage() {
  const voiceActivity = useWSEvent<VoiceActivity>('voice_activity')
  const transcriptEvent = useWSEvent<{ content?: string }>('user_transcript')
  const [voiceInfo, setVoiceInfo] = useState<VoiceStatusInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([])
  const transcriptRef = useRef<HTMLDivElement>(null)

  // Collect transcript entries
  useEffect(() => {
    if (transcriptEvent?.content) {
      setTranscript((prev) => [
        ...prev.slice(-49), // keep last 50
        { id: `${Date.now()}-${prev.length}`, content: transcriptEvent.content!, timestamp: new Date() },
      ])
    }
  }, [transcriptEvent])

  // Auto-scroll transcript
  useEffect(() => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight
    }
  }, [transcript])

  const loadVoiceInfo = useCallback(async () => {
    try {
      const data = await api.fetchVoiceStatus()
      setVoiceInfo({
        stt_model: data.stt_model,
        tts_model: data.tts_model,
        tts_speed: data.tts_speed,
      })
    } catch (e) {
      console.error('Failed to load voice status:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadVoiceInfo()
  }, [loadVoiceInfo])

  const { label, status, active } = getVoiceState(voiceActivity)
  const volumeLevel = voiceActivity?.volume_level ?? 0

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <LoadingSpinner label="Loading voice status..." />
      </div>
    )
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <PageHeader title="Voice" subtitle="Voice interaction status &amp; pipeline" />

      {/* Main status card */}
      <GlassCard className="!p-8">
        <div className="flex flex-col items-center text-center">
          {/* Glowing cyan circle that pulses with voice activity */}
          <div className="relative mb-6 flex items-center justify-center w-32 h-32">
            {/* Outer glow ring */}
            <div
              className={cn(
                'absolute w-32 h-32 rounded-full transition-opacity duration-500',
                active ? 'opacity-60 animate-voice-pulse' : 'opacity-0',
                label === 'Speaking' ? 'bg-charlie-cyan/20' : 'bg-charlie-green/20',
              )}
              style={{ filter: 'blur(20px)' }}
            />
            {/* Mid glow ring */}
            <div
              className={cn(
                'absolute w-24 h-24 rounded-full transition-opacity duration-500',
                active ? 'opacity-80 animate-voice-pulse' : 'opacity-0',
                label === 'Speaking' ? 'bg-charlie-cyan/30' : 'bg-charlie-green/30',
              )}
              style={{ filter: 'blur(10px)', animationDelay: '0.2s' }}
            />
            {/* Core circle */}
            <div
              className={cn(
                'relative w-16 h-16 rounded-full border-2 flex items-center justify-center transition-all duration-500',
                active
                  ? label === 'Speaking'
                    ? 'border-charlie-cyan bg-charlie-cyan/10'
                    : 'border-charlie-green bg-charlie-green/10'
                  : 'border-charlie-border bg-charlie-dark/60',
              )}
              style={{
                boxShadow: active
                  ? label === 'Speaking'
                    ? '0 0 30px color-mix(in srgb, var(--charlie-cyan) 30%, transparent)'
                    : '0 0 30px color-mix(in srgb, var(--charlie-green) 30%, transparent)'
                  : 'none',
              }}
            >
              <StatusDot
                status={status}
                pulse={active}
                className="!w-4 !h-4"
              />
            </div>
          </div>

          {/* Status label */}
          <h2
            className={cn(
              'text-3xl font-bold mb-2 transition-colors duration-300',
              active ? 'text-charlie-cyan' : 'text-charlie-dim',
            )}
          >
            {label}
          </h2>

          {/* Waveform */}
          <div className="w-full max-w-md my-6">
            <WaveformVisualizer
              active={active}
              volumeLevel={volumeLevel}
              className="h-16"
            />
          </div>

          {/* Volume indicator */}
          {active && (
            <div className="w-full max-w-xs mb-4">
              <div className="flex items-center justify-between text-xs text-charlie-dim mb-1">
                <span>Volume</span>
                <span>{Math.round(volumeLevel * 100)}%</span>
              </div>
              <div className="h-1.5 bg-charlie-border rounded-full overflow-hidden">
                <div
                  className="h-full bg-charlie-cyan rounded-full transition-all duration-100"
                  style={{ width: `${volumeLevel * 100}%` }}
                />
              </div>
            </div>
          )}

          {/* Current transcript */}
          {voiceActivity?.current_transcript && (
            <div className="w-full max-w-md mt-4 p-4 rounded-lg bg-charlie-dark/60 border border-charlie-border/30">
              <span className="text-[10px] text-charlie-dim uppercase font-medium block mb-2">
                Transcript
              </span>
              <p className="text-sm text-charlie-text">
                {voiceActivity.current_transcript}
              </p>
            </div>
          )}
        </div>
      </GlassCard>

      {/* Model info */}
      <GlassCard>
        <h3 className="text-sm font-semibold text-charlie-text mb-3 font-display tracking-[0.1em] uppercase">Voice Models</h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="p-3 rounded-lg bg-charlie-dark/40 border border-charlie-border/30">
            <span className="text-[10px] text-charlie-dim uppercase block mb-1">STT Model</span>
            <span className="text-sm text-charlie-text font-mono">
              {voiceInfo?.stt_model ?? 'Unknown'}
            </span>
          </div>
          <div className="p-3 rounded-lg bg-charlie-dark/40 border border-charlie-border/30">
            <span className="text-[10px] text-charlie-dim uppercase block mb-1">TTS Model</span>
            <span className="text-sm text-charlie-text font-mono">
              {voiceInfo?.tts_model ?? 'Unknown'}
            </span>
          </div>
          <div className="p-3 rounded-lg bg-charlie-dark/40 border border-charlie-border/30">
            <span className="text-[10px] text-charlie-dim uppercase block mb-1">TTS Speed</span>
            <span className="text-sm text-charlie-text font-mono">
              {voiceInfo?.tts_speed?.toFixed(1) ?? '1.0'}x
            </span>
          </div>
        </div>
      </GlassCard>

      {/* STT/TTS status indicators */}
      <GlassCard>
        <h3 className="text-sm font-semibold text-charlie-text mb-3 font-display tracking-[0.1em] uppercase">Pipeline Status</h3>
        <div className="grid grid-cols-2 gap-3">
          <div className="flex items-center gap-3 p-3 rounded-lg bg-charlie-dark/40 border border-charlie-border/30">
            <StatusDot
              status={voiceActivity?.stt_active ? 'online' : 'idle'}
              pulse={voiceActivity?.stt_active}
            />
            <div>
              <span className="text-sm text-charlie-text">STT Engine</span>
              <div className="text-xs text-charlie-dim">
                {voiceActivity?.stt_active ? 'Processing audio' : 'Standby'}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3 p-3 rounded-lg bg-charlie-dark/40 border border-charlie-border/30">
            <StatusDot
              status={voiceActivity?.tts_active ? 'online' : 'idle'}
              pulse={voiceActivity?.tts_active}
            />
            <div>
              <span className="text-sm text-charlie-text">TTS Engine</span>
              <div className="text-xs text-charlie-dim">
                {voiceActivity?.tts_active ? 'Generating speech' : 'Standby'}
              </div>
            </div>
          </div>
        </div>
      </GlassCard>

      {/* Persistent transcript log */}
      <GlassCard>
        <h3 className="text-sm font-semibold text-charlie-text mb-3 font-display tracking-[0.1em] uppercase">
          Transcript Log
        </h3>
        {transcript.length === 0 ? (
          <p className="text-xs text-charlie-dim py-4 text-center">
            No voice transcripts yet. Start speaking to see them here.
          </p>
        ) : (
          <div ref={transcriptRef} className="max-h-64 overflow-y-auto space-y-2 scrollbar-thin">
            {transcript.map((entry) => (
              <div
                key={entry.id}
                className="flex gap-3 p-2 rounded-lg bg-charlie-dark/30 border border-charlie-border/20"
              >
                <span className="text-[10px] text-charlie-dim whitespace-nowrap mt-0.5 font-mono">
                  {entry.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </span>
                <p className="text-sm text-charlie-text flex-1">{entry.content}</p>
              </div>
            ))}
          </div>
        )}
      </GlassCard>
    </div>
  )
}
