'use client'

import { useEffect } from 'react'
import { wsManager } from '@/lib/ws'
import { useDashboardStore } from '@/lib/store'

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

    // Subscribe to WS events
    // Backend lowercases WS_FORWARD_TYPES: PHASE→phase, VOICE_ACTIVITY→voice_activity, etc.
    const unsubPhase = wsManager.subscribe('phase', (data) => {
      setCurrentPhase((data.phase as string) || (data.type as string) || 'idle')
    })

    const unsubApproval = wsManager.subscribe('approval_pending', () => {
      const store = useDashboardStore.getState()
      setPendingApprovals(store.pendingApprovals + 1)
    })

    const unsubApprovalResolved = wsManager.subscribe('approval_resolved', () => {
      const store = useDashboardStore.getState()
      setPendingApprovals(Math.max(0, store.pendingApprovals - 1))
    })

    const unsubVoice = wsManager.subscribe('voice_activity', (data) => {
      setVoiceActivity({
        is_listening: data.is_listening as boolean ?? false,
        is_speaking: data.is_speaking as boolean ?? false,
        stt_active: data.stt_active as boolean ?? false,
        tts_active: data.tts_active as boolean ?? false,
        wake_word_detected: data.wake_word_detected as boolean ?? false,
        current_transcript: data.current_transcript as string | undefined,
        volume_level: data.volume_level as number | undefined,
      })
    })

    const unsubPhoenixAlert = wsManager.subscribe('phoenix_alert', (data) => {
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

    return () => {
      clearInterval(connInterval)
      unsubPhase()
      unsubApproval()
      unsubApprovalResolved()
      unsubVoice()
      unsubPhoenixAlert()
      unsubResearchResult()
      unsubResearchFollowup()
    }
  }, [setConnectionStatus, setCurrentPhase, setPendingApprovals, setVoiceActivity, setResearchResult, setResearchFollowup, setResearchPanelOpen])

  return null
}
