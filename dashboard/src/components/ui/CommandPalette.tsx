'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { motion, AnimatePresence } from 'framer-motion'
import { Search, X, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  Activity,
  Shield,
  Brain,
  Puzzle,
  Zap,
  Settings,
  MessageSquare,
  ListTodo,
  Server,
  Users,
  Terminal,
  BarChart3,
  Sparkles,
  History,
  ScrollText,
  Database,
  TrendingUp,
} from 'lucide-react'
import { searchMemory, fetchTasks, fetchChatHistory } from '@/lib/api'

interface PaletteItem {
  id: string
  label: string
  description: string
  icon: LucideIcon
  href?: string
  action?: () => void
  group: string
}

const pages: PaletteItem[] = [
  { id: 'status', label: 'Status', description: 'System health and metrics', icon: Activity, href: '/status', group: 'Pages' },
  { id: 'briefing', label: 'Briefing', description: 'Daily intelligent summary', icon: Sparkles, href: '/briefing', group: 'Pages' },
  { id: 'chat', label: 'Conversation', description: 'Talk to Charlie', icon: MessageSquare, href: '/chat', group: 'Pages' },
  { id: 'tasks', label: 'Tasks', description: 'Active and pending operations', icon: ListTodo, href: '/tasks', group: 'Pages' },
  { id: 'approvals', label: 'Approvals', description: 'Review pending risk actions', icon: Shield, href: '/approvals', group: 'Pages' },
  { id: 'memory', label: 'Knowledge', description: 'System memory and facts', icon: History, href: '/memory', group: 'Pages' },
  { id: 'evolution', label: 'Evolution', description: 'Learning and drift logs', icon: TrendingUp, href: '/evolution', group: 'Pages' },
  { id: 'automation', label: 'Automation', description: 'Managed workflows', icon: Zap, href: '/automation', group: 'Pages' },
  { id: 'tools', label: 'Tools', description: 'Registered engine capabilities', icon: Terminal, href: '/tools', group: 'Pages' },
  { id: 'settings', label: 'Settings', description: 'Dashboard configuration', icon: Settings, href: '/settings', group: 'Pages' },
]

interface CommandPaletteProps {
  open: boolean
  onClose: () => void
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const router = useRouter()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<PaletteItem[]>([])
  const [searching, setSearching] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const filteredPages = pages.filter(p => 
    p.label.toLowerCase().includes(query.toLowerCase()) || 
    p.description.toLowerCase().includes(query.toLowerCase())
  )

  const executeItem = useCallback((item: PaletteItem) => {
    if (item.href) {
      router.push(item.href)
    } else if (item.action) {
      item.action()
    }
    onClose()
  }, [router, onClose])

  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 10)
      setQuery('')
    }
  }, [open])

  useEffect(() => {
    if (query.length < 3) {
      setResults([])
      return
    }

    const timer = setTimeout(async () => {
      setSearching(true)
      try {
        const memResults = await searchMemory(query)
        const chatResults = await fetchChatHistory() // Simple mock logic for searching
        
        const mappedResults: PaletteItem[] = [
          ...memResults.results.map((m, i) => ({
            id: `mem-${i}`,
            label: m.content.slice(0, 40),
            description: 'Memory Entry',
            icon: Database,
            group: 'Knowledge',
            action: () => router.push(`/memory?t=${m.timestamp}`)
          })),
          ...chatResults.messages.filter(c => c.content.toLowerCase().includes(query.toLowerCase())).map(c => ({
            id: `chat-${c.id}`,
            label: c.content.slice(0, 40),
            description: 'Chat Message',
            icon: MessageSquare,
            group: 'History',
            href: '/chat'
          }))
        ]
        setResults(mappedResults)
      } catch (e) {
        console.error('Search error:', e)
      } finally {
        setSearching(false)
      }
    }, 300)

    return () => clearTimeout(timer)
  }, [query, router])

  const allResults = [...filteredPages, ...results]

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
      } else if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedIndex((i) => Math.min(i + 1, allResults.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedIndex((i) => Math.max(i - 1, 0))
      } else if (e.key === 'Enter' && allResults[selectedIndex]) {
        executeItem(allResults[selectedIndex])
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, allResults, selectedIndex, onClose, executeItem])

  useEffect(() => {
    setSelectedIndex(0)
  }, [query])

  useEffect(() => {
    if (!listRef.current) return
    const selected = listRef.current.children[selectedIndex] as HTMLElement
    selected?.scrollIntoView({ block: 'nearest' })
  }, [selectedIndex])

  let lastGroup = ''

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 bg-black/80 backdrop-blur-sm z-[200]"
            onClick={onClose}
          />

          <motion.div
            initial={{ opacity: 0, scale: 0.98, y: -10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98, y: -10 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            className="fixed top-[15%] left-1/2 -translate-x-1/2 w-full max-w-lg z-[201]"
          >
            <div className="premium-card overflow-hidden shadow-premium">
              <div className="flex items-center gap-3 px-5 py-4 border-b border-charlie-border">
                <Search size={18} className="text-charlie-dim flex-shrink-0" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search pages, knowledge, actions..."
                  aria-label="Search commands and resources"
                  className="flex-1 bg-transparent text-charlie-text text-sm outline-none placeholder-charlie-dim font-sans"
                />
                {searching && (
                  <div className="w-4 h-4 border-2 border-charlie-border border-t-charlie-text rounded-full animate-spin" />
                )}
                <button onClick={onClose} className="text-charlie-dim hover:text-charlie-text transition-colors" aria-label="Close command palette">
                  <X size={16} />
                </button>
              </div>

              <div ref={listRef} className="max-h-[400px] overflow-y-auto py-2">
                {allResults.length === 0 && !searching && (
                  <div className="px-5 py-12 text-center text-charlie-dim text-sm">
                    No results found for &quot;{query}&quot;
                  </div>
                )}
                {allResults.map((item, i) => {
                  const Icon = item.icon
                  const showGroup = item.group !== lastGroup
                  lastGroup = item.group
                  return (
                    <div key={item.id}>
                      {showGroup && (
                        <div className="px-5 pt-3 pb-1 text-[10px] font-semibold tracking-widest uppercase text-charlie-dim">
                          {item.group}
                        </div>
                      )}
                      <button
                        onClick={() => executeItem(item)}
                        onMouseEnter={() => setSelectedIndex(i)}
                        className={cn(
                          'w-full flex items-center gap-3 px-5 py-2.5 text-left transition-all duration-200 cursor-pointer',
                          i === selectedIndex
                            ? 'bg-charlie-text/10 text-charlie-text'
                            : 'text-charlie-dim hover:text-charlie-text hover:bg-charlie-text/5',
                        )}
                      >
                        <Icon size={16} className="flex-shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium">{item.label}</div>
                          <div className="text-xs text-charlie-dim truncate">{item.description}</div>
                        </div>
                        {i === selectedIndex && (
                          <kbd className="text-[10px] text-charlie-dim bg-charlie-text/5 px-1.5 py-0.5 rounded border border-charlie-border font-sans">
                            Enter
                          </kbd>
                        )}
                      </button>
                    </div>
                  )
                })}
              </div>

              <div className="px-5 py-3 border-t border-charlie-border flex items-center gap-6 text-[10px] text-charlie-dim">
                <span className="flex items-center gap-1.5"><kbd className="bg-charlie-text/5 px-1.5 py-0.5 rounded border border-charlie-border">↑↓</kbd> Navigate</span>
                <span className="flex items-center gap-1.5"><kbd className="bg-charlie-text/5 px-1.5 py-0.5 rounded border border-charlie-border">Enter</kbd> Select</span>
                <span className="flex items-center gap-1.5"><kbd className="bg-charlie-text/5 px-1.5 py-0.5 rounded border border-charlie-border">Esc</kbd> Close</span>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
