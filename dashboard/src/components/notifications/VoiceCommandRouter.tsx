'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useWSEvent } from '@/lib/ws'
import { addToast } from './ToastContainer'
import { fetchApprovals, approveAction, runBriefing } from '@/lib/api'

interface VoiceCommand {
  intent: 'navigate' | 'action' | 'search' | 'conversation'
  target?: string
  query?: string
  action?: string
}

const routeMap: Record<string, string> = {
  status: '/status',
  home: '/',
  voice: '/',
  chat: '/chat',
  tasks: '/tasks',
  approvals: '/approvals',
  memory: '/memory',
  briefing: '/briefing',
  agents: '/agents',
  tools: '/tools',
  integrations: '/integrations',
  automation: '/automation',
  settings: '/settings',
  mcp: '/mcp',
  search: '/search',
  logs: '/logs',
  analytics: '/analytics',
  skills: '/skills',
  evolution: '/evolution',
}

export function VoiceCommandRouter() {
  const router = useRouter()
  const voiceCommand = useWSEvent<VoiceCommand>('voice_command')

  useEffect(() => {
    if (!voiceCommand) return

    switch (voiceCommand.intent) {
      case 'navigate':
        if (voiceCommand.target) {
          const route = routeMap[voiceCommand.target.toLowerCase()]
          if (route) {
            router.push(route)
            addToast({ type: 'info', message: `Navigating to ${voiceCommand.target}` })
          }
        }
        break

      case 'action':
        if (voiceCommand.action) {
          handleAction(voiceCommand.action)
        }
        break

      case 'search':
        if (voiceCommand.query) {
          router.push(`/search?q=${encodeURIComponent(voiceCommand.query)}`)
          addToast({ type: 'info', message: `Searching: ${voiceCommand.query}` })
        }
        break

      case 'conversation':
        break
    }
  }, [voiceCommand, router])

  return null
}

function handleAction(action: string) {
  const actionLower = action.toLowerCase()

  if (actionLower.includes('approve')) {
    fetchApprovals()
      .then((data) => {
        if (data.pending?.length > 0) {
          return approveAction(data.pending[0].id)
        }
      })
      .then(() => addToast({ type: 'success', message: 'Action approved via voice' }))
      .catch(() => addToast({ type: 'error', message: 'Could not approve action' }))
  } else if (actionLower.includes('briefing')) {
    runBriefing()
      .then(() => addToast({ type: 'info', message: 'Generating briefing...' }))
      .catch(() => addToast({ type: 'error', message: 'Could not generate briefing' }))
  }
}
