'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { Sidebar } from '@/components/layout/Sidebar'
import { StatusBar } from '@/components/layout/StatusBar'
import { WSBridge } from '@/components/WSBridge'
import { ResearchPanel } from '@/components/research/ResearchPanel'
import { ToastContainer } from '@/components/notifications/ToastContainer'
import { VoiceCommandRouter } from '@/components/notifications/VoiceCommandRouter'
import { CommandPalette } from '@/components/ui/CommandPalette'
import { ErrorBoundary } from '@/components/ui/ErrorBoundary'
import { useDashboardStore } from '@/lib/store'
import { sendMessage, fetchSettings } from '@/lib/api'
import { cn } from '@/lib/utils'

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const [paletteOpen, setPaletteOpen] = useState(false)
  const router = useRouter()
  const pathname = usePathname()
  const collapsed = useDashboardStore((s) => s.sidebarCollapsed)
  const hovered = useDashboardStore((s) => s.sidebarHovered)
  const theme = useDashboardStore((s) => s.theme)
  const researchResult = useDashboardStore((s) => s.researchResult)
  const researchFollowup = useDashboardStore((s) => s.researchFollowup)
  const researchPanelOpen = useDashboardStore((s) => s.researchPanelOpen)
  const setResearchPanelOpen = useDashboardStore((s) => s.setResearchPanelOpen)

  // Apply theme class on mount and changes
  useEffect(() => {
    document.documentElement.classList.toggle('light', theme === 'light')
    document.documentElement.classList.toggle('dark', theme === 'dark')
  }, [theme])

  // Auto-redirect to /setup if not configured
  useEffect(() => {
    if (pathname === '/setup') return
    fetchSettings()
      .then((settings) => {
        const s = settings as Record<string, unknown>
        if (s.setup_complete === false) {
          router.push('/setup')
        }
      })
      .catch(() => {
        // Can't reach daemon — don't redirect, let user proceed
      })
  }, [pathname, router])

  // Global Ctrl+K handler for command palette
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setPaletteOpen((prev) => !prev)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // Global keyboard shortcuts
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.ctrlKey && e.shiftKey && e.key === 'M') {
        e.preventDefault()
        router.push('/memory')
      }
      if (e.ctrlKey && e.key === '/') {
        e.preventDefault()
        // Toggle command palette as help overlay
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }))
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [router])

  const isExpanded = !collapsed || hovered

  const handleSuggestionClick = useCallback((suggestion: string) => {
    sendMessage(suggestion).catch(() => {})
    setResearchPanelOpen(false)
  }, [setResearchPanelOpen])

  const handleQuestionAnswer = useCallback((answer: string) => {
    sendMessage(answer).catch(() => {})
    setResearchPanelOpen(false)
  }, [setResearchPanelOpen])

  return (
    <>

      <WSBridge />
      <VoiceCommandRouter />
      <ToastContainer />
      <Sidebar />
      <main
        className={cn(
          'pt-4 px-6 pb-16 transition-all duration-300 ease-out relative z-10',
          isExpanded ? 'ml-56' : 'ml-14',
        )}
      >
        <ErrorBoundary>
          {children}
        </ErrorBoundary>
      </main>
      <StatusBar />
      <ResearchPanel
        result={researchResult}
        followup={researchFollowup}
        open={researchPanelOpen}
        onClose={() => setResearchPanelOpen(false)}
        onSuggestionClick={handleSuggestionClick}
        onQuestionAnswer={handleQuestionAnswer}
      />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </>
  )
}
