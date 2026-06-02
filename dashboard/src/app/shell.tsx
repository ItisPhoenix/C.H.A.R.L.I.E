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
import { Button } from '@/components/ui/Button'
import { AlertTriangle, X } from 'lucide-react'

function BrainDisconnectedBanner() {
  const brainDisconnected = useDashboardStore((s) => s.brainDisconnected)
  const dismissed = useDashboardStore((s) => s.brainBannerDismissed)
  const setDismissed = useDashboardStore((s) => s.setBrainBannerDismissed)
  const collapsed = useDashboardStore((s) => s.sidebarCollapsed)
  if (!brainDisconnected || dismissed) return null

  const isExpanded = !collapsed

  return (
    <div
      className={cn(
        'fixed top-0 right-0 z-40 flex items-center justify-between gap-3 px-4 py-2.5',
        'bg-amber-950/90 backdrop-blur-sm border-b border-amber-500/30 text-amber-200 text-sm',
        'transition-all duration-300',
        isExpanded ? 'left-56' : 'left-14',
      )}
    >
      <div className="flex items-center gap-2">
        <AlertTriangle size={16} className="text-amber-400 flex-shrink-0" />
        <span className="font-medium">Brain Disconnected</span>
        <span className="text-amber-300/70 hidden sm:inline">-- some features unavailable</span>
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setDismissed(true)}
        className="!p-0.5 !text-amber-400 hover:!text-amber-200"
        aria-label="Dismiss banner"
      >
        <X size={16} />
      </Button>
    </div>
  )
}

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const [paletteOpen, setPaletteOpen] = useState(false)
  const router = useRouter()
  const pathname = usePathname()
  const collapsed = useDashboardStore((s) => s.sidebarCollapsed)
  const researchResult = useDashboardStore((s) => s.researchResult)
  const researchFollowup = useDashboardStore((s) => s.researchFollowup)
  const researchPanelOpen = useDashboardStore((s) => s.researchPanelOpen)
  const setResearchPanelOpen = useDashboardStore((s) => s.setResearchPanelOpen)

  // Dark mode only
  useEffect(() => {
    document.documentElement.classList.remove('light')
    document.documentElement.classList.add('dark')
  }, [])

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

  const isExpanded = !collapsed

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
      <BrainDisconnectedBanner />
      <main
        className={cn(
          'pt-4 px-6 pb-24 transition-all duration-300 ease-out relative z-10',
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
