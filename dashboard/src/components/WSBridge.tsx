'use client'

import { useEffect } from 'react'
import { wsManager } from '@/lib/ws'
import { useDashboardStore } from '@/lib/store'
import type { VoiceActivity } from '@/lib/types'
import { checkBrainStatus } from '@/lib/api'
import { addToast } from '@/components/notifications/ToastContainer'

export function WSBridge() {
  const setConnectionStatus = useDashboardStore((s) => s.setConnectionStatus)
  const setCurrentPhase = useDashboardStore((s) => s.setCurrentPhase)
  const setPendingApprovals = useDashboardStore((s) => s.setPendingApprovals)
  const setVoiceActivity = useDashboardStore((s) => s.setVoiceActivity)
  const setResearchResult = useDashboardStore((s) => s.setResearchResult)
  const setResearchFollowup = useDashboardStore((s) => s.setResearchFollowup)
  const setResearchPanelOpen = useDashboardStore((s) => s.setResearchPanelOpen)

  useEffect(() => {
    // Connection state polling
    const connInterval = setInterval(() => {
      setConnectionStatus(wsManager.connected ? 'connected' : 'disconnected')
    }, 1000)

    // Brain status polling (every 10s)
    const brainInterval = setInterval(async () => {
      const ok = await checkBrainStatus()
      const prev = useDashboardStore.getState().brainDisconnected
      useDashboardStore.getState().setBrainDisconnected(!ok)
      // When brain reconnects, un-dismiss the banner so it disappears
      if (ok && prev) {
        useDashboardStore.getState().setBrainBannerDismissed(false)
      }
    }, 10000)
    // Also check immediately on mount
    checkBrainStatus().then((ok) => {
      useDashboardStore.getState().setBrainDisconnected(!ok)
    })

    // Subscribe to WS events (must match STATUS_EVENT_MAP in charlie/watchdog/status_events.py)
    const unsubPhase = wsManager.subscribe('phase_change', (data) => {
      setCurrentPhase((data.phase as string) || (data.type as string) || 'idle')
    })

    const unsubApproval = wsManager.subscribe('approval_pending', () => {
      useDashboardStore.setState((s) => ({ pendingApprovals: s.pendingApprovals + 1 }))
    })

    const unsubApprovalResolved = wsManager.subscribe('approval_resolved', () => {
      useDashboardStore.setState((s) => ({ pendingApprovals: Math.max(0, s.pendingApprovals - 1) }))
    })

    const unsubVoice = wsManager.subscribe('voice_activity', (data) => {
      // Merge with current state. Backend may emit partial events (e.g. only
      // is_listening) — preserve fields the incoming event omits. Matches the
      // sticky-muted behavior at charlie/watchdog/ipc_bridge.py.
      const current = useDashboardStore.getState().voiceActivity
      const merged: VoiceActivity = {
        is_listening: typeof data.is_listening === 'boolean' ? data.is_listening : current?.is_listening ?? false,
        is_speaking: typeof data.is_speaking === 'boolean' ? data.is_speaking : current?.is_speaking ?? false,
        stt_active: typeof data.stt_active === 'boolean' ? data.stt_active : current?.stt_active ?? false,
        tts_active: typeof data.tts_active === 'boolean' ? data.tts_active : current?.tts_active ?? false,
        wake_word_detected: typeof data.wake_word_detected === 'boolean' ? data.wake_word_detected : current?.wake_word_detected ?? false,
        current_transcript: typeof data.current_transcript === 'string' ? data.current_transcript : current?.current_transcript,
        volume_level: typeof data.volume_level === 'number' ? data.volume_level : current?.volume_level,
        muted: typeof data.muted === 'boolean' ? data.muted : current?.muted ?? false,
      }
      setVoiceActivity(merged)
    })

    const unsubPhoenixAlert = wsManager.subscribe('subsystem_failure', (data) => {
      // Forward to notification system
      const event = new CustomEvent('charlie-notification', {
        detail: { type: 'alert', title: 'System Alert', message: data.content || 'Alert' },
      })
      window.dispatchEvent(event)
    })

    // Research events
    const unsubResearchResult = wsManager.subscribe('research_result', (data) => {
      const content = data.content as Record<string, unknown> | undefined
      if (content) {
        setResearchResult({
          topic: (content.topic as string) || 'Research',
          findings: (content.findings as string[]) || [],
          sources: (content.sources as Array<{ title: string; url: string }>) || [],
          summary: (content.summary as string) || '',
        })
        setResearchPanelOpen(true)
      }
    })

    const unsubResearchFollowup = wsManager.subscribe('research_followup', (data) => {
      const content = data.content as Record<string, unknown> | undefined
      if (content) {
        setResearchFollowup({
          questions: (content.questions as string[]) || [],
          suggestions: (content.suggestions as string[]) || [],
          clarifying_question: (content.clarifying_question as string) || '',
        })
      }
    })

    // Toast notifications for key events
    const unsubToolComplete = wsManager.subscribe('tool_completed', (data) => {
      addToast({
        type: 'success',
        title: 'Tool Completed',
        message: `${data.name || 'Tool'} finished`,
      })
    })

    const unsubApprovalToast = wsManager.subscribe('approval_pending', () => {
      addToast({
        type: 'warning',
        title: 'Approval Required',
        message: 'A new action needs your approval',
      })
    })

    const unsubSubsystemCrash = wsManager.subscribe('subsystem_crashed', (data) => {
      addToast({
        type: 'error',
        title: 'Subsystem Crashed',
        message: `${data.name || 'Subsystem'} has crashed`,
      })
    })

    return () => {
      clearInterval(connInterval)
      clearInterval(brainInterval)
      unsubPhase()
      unsubApproval()
      unsubApprovalResolved()
      unsubVoice()
      unsubPhoenixAlert()
      unsubResearchResult()
      unsubResearchFollowup()
      unsubToolComplete()
      unsubApprovalToast()
      unsubSubsystemCrash()
    }
  }, [setConnectionStatus, setCurrentPhase, setPendingApprovals, setVoiceActivity, setResearchResult, setResearchFollowup, setResearchPanelOpen])

  return null
}
