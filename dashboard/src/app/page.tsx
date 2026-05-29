'use client'

import { useEffect, useState, useRef, useCallback } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { StatusDot } from '@/components/ui/StatusDot'
import { Metric } from '@/components/ui/Metric'
import { HudCorners } from '@/components/background/HudCorners'
import { useDashboardStore } from '@/lib/store'
import { formatUptime } from '@/lib/utils'
import { fetchTasks, fetchToolLog, fetchStatus } from '@/lib/api'
import type { Task, ToolExecution } from '@/lib/types'
import { Mic, MicOff, Activity, Zap, Brain } from 'lucide-react'

// Voice Orb Component
function VoiceOrb() {
  const voice = useDashboardStore((s) => s.voiceActivity)
  const phase = useDashboardStore((s) => s.currentPhase)

  const isListening = voice?.is_listening ?? false
  const isSpeaking = voice?.is_speaking ?? false
  const isProcessing = phase === 'processing'

  let orbColor = 'rgba(100, 116, 139, 0.4)' // idle
  let glowColor = 'rgba(100, 116, 139, 0.15)'
  let label = 'Idle'
  let pulseSpeed = '3s'

  if (isSpeaking) {
    orbColor = 'rgba(34, 197, 94, 0.6)'
    glowColor = 'rgba(34, 197, 94, 0.3)'
    label = 'Speaking'
    pulseSpeed = '1s'
  } else if (isProcessing) {
    orbColor = 'rgba(245, 158, 11, 0.6)'
    glowColor = 'rgba(245, 158, 11, 0.3)'
    label = 'Processing'
    pulseSpeed = '0.6s'
  } else if (isListening) {
    orbColor = 'rgba(0, 212, 255, 0.6)'
    glowColor = 'rgba(0, 212, 255, 0.3)'
    label = 'Listening'
    pulseSpeed = '2s'
  }

  return (
    <div className="flex flex-col items-center justify-center py-8">
      {/* Outer ring */}
      <div
        className="relative w-48 h-48 flex items-center justify-center"
        style={{
          animation: `pulse ${pulseSpeed} ease-in-out infinite`,
        }}
      >
        {/* Outer glow ring */}
        <div
          className="absolute inset-0 rounded-full"
          style={{
            border: `2px solid ${orbColor}`,
            boxShadow: `0 0 30px ${glowColor}, 0 0 60px ${glowColor}`,
            animation: `pulse ${pulseSpeed} ease-in-out infinite`,
          }}
        />
        {/* Middle ring */}
        <div
          className="absolute inset-4 rounded-full"
          style={{
            border: `1px solid ${orbColor}`,
            boxShadow: `0 0 20px ${glowColor}`,
            opacity: 0.7,
          }}
        />
        {/* Inner orb */}
        <div
          className="w-24 h-24 rounded-full flex items-center justify-center"
          style={{
            background: `radial-gradient(circle, ${orbColor}, transparent)`,
            boxShadow: `0 0 40px ${glowColor}, 0 0 80px ${glowColor}`,
          }}
        >
          {isListening ? (
            <Mic size={32} className="text-white drop-shadow-lg" />
          ) : isSpeaking ? (
            <Activity size={32} className="text-white drop-shadow-lg" />
          ) : (
            <MicOff size={32} className="text-charlie-dim" />
          )}
        </div>
      </div>

      {/* State label */}
      <div className="mt-4 flex items-center gap-2">
        <StatusDot
          status={isListening ? 'online' : isSpeaking ? 'online' : isProcessing ? 'warning' : 'idle'}
          pulse={isListening || isSpeaking || isProcessing}
        />
        <span className="font-display text-sm tracking-[0.15em] uppercase" style={{ color: orbColor.replace('0.6', '1').replace('0.4', '0.8') }}>
          {label}
        </span>
      </div>
    </div>
  )
}

// Transcript Display — reads from chat history API
function TranscriptDisplay() {
  const [transcript, setTranscript] = useState<Array<{ role: 'user' | 'assistant'; text: string; time: string }>>([])
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    async function loadTranscript() {
      try {
        const { fetchChatHistory } = await import('@/lib/api')
        const data = await fetchChatHistory()
        const msgs = (data.messages || []).slice(-10).map((m) => ({
          role: m.role === 'user' ? 'user' as const : 'assistant' as const,
          text: m.content || '',
          time: m.timestamp ? new Date(m.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '',
        }))
        if (msgs.length > 0) setTranscript(msgs)
      } catch {}
    }
    loadTranscript()
  }, [])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [transcript])

  return (
    <GlassCard className="max-w-2xl mx-auto">
      <div className="flex items-center gap-2 mb-3 pb-3 border-b border-charlie-border">
        <Brain size={16} className="text-charlie-cyan" />
        <span className="font-display text-xs tracking-[0.1em] text-charlie-cyan uppercase">Live Transcript</span>
      </div>
      <div ref={scrollRef} className="space-y-3 max-h-48 overflow-y-auto">
        {transcript.map((msg, i) => (
          <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : ''}`}>
            <div className={`max-w-[80%] ${msg.role === 'user' ? 'order-1' : ''}`}>
              <div className={`text-xs mb-1 ${msg.role === 'user' ? 'text-right' : ''}`}>
                <span className="text-charlie-dim">{msg.role === 'user' ? 'You' : 'CHARLIE'}</span>
                <span className="text-charlie-dim/50 ml-2">{msg.time}</span>
              </div>
              <div
                className={`text-sm font-body p-3 rounded-lg ${
                  msg.role === 'user'
                    ? 'bg-charlie-cyan/10 border border-charlie-cyan/20 text-charlie-text'
                    : 'bg-charlie-card border border-charlie-border text-charlie-text'
                }`}
              >
                {msg.text}
              </div>
            </div>
          </div>
        ))}
      </div>
    </GlassCard>
  )
}

// Key Metrics — real data from API
function KeyMetrics() {
  const daemon = useDashboardStore((s) => s.daemonStatus)
  const setDaemonStatus = useDashboardStore((s) => s.setDaemonStatus)
  const phase = useDashboardStore((s) => s.currentPhase)
  const [currentTask, setCurrentTask] = useState<Task | null>(null)
  const [recentTools, setRecentTools] = useState<ToolExecution[]>([])

  const loadMetrics = useCallback(async () => {
    try {
      const [tasksData, toolsData, statusData] = await Promise.all([fetchTasks(), fetchToolLog(), fetchStatus()])
      const active = (tasksData.tasks || []).find((t) => t.status === 'running' || t.status === 'pending')
      setCurrentTask(active || null)
      setRecentTools((toolsData.executions || []).slice(0, 3))
      if (statusData) {
        setDaemonStatus({
          uptime_seconds: statusData.uptime_seconds || 0,
          subsystems: statusData.subsystems || {},
          system: statusData.system || { cpu: 0, ram: 0 },
        })
      }
    } catch {}
  }, [setDaemonStatus])

  useEffect(() => {
    loadMetrics()
    const interval = setInterval(loadMetrics, 10000)
    return () => clearInterval(interval)
  }, [loadMetrics])

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-4xl mx-auto">
      {/* Current Task */}
      <HudCorners>
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Zap size={16} className="text-charlie-cyan" />
            <span className="font-display text-xs tracking-[0.1em] text-charlie-cyan uppercase">Current Task</span>
          </div>
          {currentTask ? (
            <>
              <div className="font-body text-sm text-charlie-text mb-1">{currentTask.name || currentTask.id}</div>
              <Badge variant="cyan">{currentTask.status || 'active'}</Badge>
            </>
          ) : (
            <div className="text-charlie-dim text-sm font-body">No active tasks</div>
          )}
        </GlassCard>
      </HudCorners>

      {/* Recent Activity */}
      <HudCorners>
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Activity size={16} className="text-charlie-green" />
            <span className="font-display text-xs tracking-[0.1em] text-charlie-green uppercase">Recent Activity</span>
          </div>
          <div className="space-y-2">
            {recentTools.length > 0 ? recentTools.map((item, i) => (
              <div key={i} className="flex items-center justify-between text-xs">
                <span className="font-mono text-charlie-text">{item.tool_name || 'unknown'}</span>
                <div className="flex items-center gap-2">
                  <span className="text-charlie-dim">{item.duration_ms ? `${item.duration_ms}ms` : ''}</span>
                  <StatusDot status={item.status === 'success' ? 'online' : item.status === 'error' ? 'error' : 'warning'} />
                </div>
              </div>
            )) : (
              <div className="text-charlie-dim text-xs">No recent activity</div>
            )}
          </div>
        </GlassCard>
      </HudCorners>

      {/* System Health */}
      <HudCorners>
        <GlassCard className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Brain size={16} className="text-charlie-purple" />
            <span className="font-display text-xs tracking-[0.1em] text-charlie-purple uppercase">System Health</span>
          </div>
          {daemon ? (
            <div className="space-y-3">
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-charlie-dim">CPU</span>
                  <span className="font-mono text-charlie-text">{daemon.system.cpu}%</span>
                </div>
                <div className="w-full bg-charlie-border rounded-full h-1.5">
                  <div
                    className={`h-1.5 rounded-full transition-all duration-500 ${
                      daemon.system.cpu > 80 ? 'bg-charlie-red' : daemon.system.cpu > 60 ? 'bg-charlie-amber' : 'bg-charlie-green'
                    }`}
                    style={{ width: `${daemon.system.cpu}%` }}
                  />
                </div>
              </div>
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-charlie-dim">RAM</span>
                  <span className="font-mono text-charlie-text">{daemon.system.ram}%</span>
                </div>
                <div className="w-full bg-charlie-border rounded-full h-1.5">
                  <div
                    className={`h-1.5 rounded-full transition-all duration-500 ${
                      daemon.system.ram > 80 ? 'bg-charlie-red' : daemon.system.ram > 60 ? 'bg-charlie-amber' : 'bg-charlie-green'
                    }`}
                    style={{ width: `${daemon.system.ram}%` }}
                  />
                </div>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-charlie-dim">Uptime</span>
                <span className="font-mono text-charlie-text">{formatUptime(daemon.uptime_seconds)}</span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-charlie-dim">Phase</span>
                <span className="font-mono text-charlie-cyan capitalize">{phase}</span>
              </div>
            </div>
          ) : (
            <div className="text-charlie-dim text-sm">Connecting...</div>
          )}
        </GlassCard>
      </HudCorners>
    </div>
  )
}

// Main Voice Home Page
export default function VoiceHomePage() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[calc(100vh-120px)]">
      {/* Voice Orb — Hero */}
      <VoiceOrb />

      {/* Live Transcript */}
      <div className="w-full mt-6 mb-8">
        <TranscriptDisplay />
      </div>

      {/* Key Metrics */}
      <KeyMetrics />
    </div>
  )
}
