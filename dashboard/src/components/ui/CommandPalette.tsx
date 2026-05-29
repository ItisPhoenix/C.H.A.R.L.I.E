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
  FileText,
  ListTodo,
  Server,
  Users,
  Globe,
  Terminal,
  Mic,
  Search as SearchIcon,
  BarChart3,
  Sparkles,
  History,
  ScrollText,
  Database,
} from 'lucide-react'
import { searchMemory, fetchTasks, fetchChatHistory } from '@/lib/api'

interface PaletteItem {
  id: string
  label: string
  description: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  icon: React.ComponentType<any>
  href?: string
  action?: () => void
  group: string
}

const pages: PaletteItem[] = [
  { id: 'voice', label: 'Voice', description: 'Voice cockpit (home)', icon: Mic, href: '/', group: 'Pages' },
  { id: 'status', label: 'Status', description: 'System status dashboard', icon: Activity, href: '/status', group: 'Pages' },
  { id: 'chat', label: 'Chat', description: 'AI conversation', icon: MessageSquare, href: '/chat', group: 'Pages' },
  { id: 'tasks', label: 'Tasks', description: 'Task queue', icon: ListTodo, href: '/tasks', group: 'Pages' },
  { id: 'memory', label: 'Memory', description: 'Knowledge graph', icon: Brain, href: '/memory', group: 'Pages' },
  { id: 'briefing', label: 'Briefing', description: 'Daily briefing', icon: FileText, href: '/briefing', group: 'Pages' },
  { id: 'agents', label: 'Agents', description: 'Agent orchestrator', icon: Users, href: '/agents', group: 'Pages' },
  { id: 'search', label: 'Search', description: 'Unified search', icon: SearchIcon, href: '/search', group: 'Pages' },
  { id: 'tools', label: 'Tools', description: 'Tool execution log', icon: Terminal, href: '/tools', group: 'Pages' },
  { id: 'mcp', label: 'MCP', description: 'MCP server management', icon: Server, href: '/mcp', group: 'Pages' },
  { id: 'integrations', label: 'Integrations', description: 'Integration health', icon: Puzzle, href: '/integrations', group: 'Pages' },
  { id: 'automation', label: 'Automation', description: 'Automation rules', icon: Zap, href: '/automation', group: 'Pages' },
  { id: 'skills', label: 'Skills', description: 'Skill management', icon: Sparkles, href: '/skills', group: 'Pages' },
  { id: 'evolution', label: 'Evolution', description: 'Evolution history', icon: History, href: '/evolution', group: 'Pages' },
  { id: 'approvals', label: 'Approvals', description: 'Pending approvals', icon: Shield, href: '/approvals', group: 'Pages' },
  { id: 'settings', label: 'Settings', description: 'Daemon settings', icon: Settings, href: '/settings', group: 'Pages' },
  { id: 'globe', label: 'Globe', description: '3D globe view', icon: Globe, href: '/globe', group: 'Pages' },
  { id: 'logs', label: 'Logs', description: 'System logs', icon: ScrollText, href: '/logs', group: 'Pages' },
  { id: 'analytics', label: 'Analytics', description: 'Usage analytics', icon: BarChart3, href: '/analytics', group: 'Pages' },
]

interface CommandPaletteProps {
  open: boolean
  onClose: () => void
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const router = useRouter()
  const [query, setQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [dataResults, setDataResults] = useState<PaletteItem[]>([])
  const [searching, setSearching] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // Filter pages by query
  const pageResults = query.length === 0
    ? pages
    : pages.filter(
        (p) =>
          p.label.toLowerCase().includes(query.toLowerCase()) ||
          p.description.toLowerCase().includes(query.toLowerCase()),
      )

  // Search across data sources when query is long enough
  useEffect(() => {
    if (query.length < 2) {
      setDataResults([])
      return
    }

    setSearching(true)
    const timer = setTimeout(async () => {
      const results: PaletteItem[] = []

      try {
        // Search memory
        const memData = await searchMemory(query)
        for (const entry of (memData.results || []).slice(0, 3)) {
          results.push({
            id: `mem-${entry.timestamp}-${results.length}`,
            label: entry.content?.slice(0, 60) || 'Memory entry',
            description: `Memory · ${entry.source || 'unknown'}`,
            icon: Database,
            href: '/memory',
            group: 'Memory',
          })
        }
      } catch {}

      try {
        // Search tasks
        const taskData = await fetchTasks()
        const q = query.toLowerCase()
        for (const task of (taskData.tasks || []).filter(
          (t) => t.name?.toLowerCase().includes(q) || t.result?.toLowerCase().includes(q)
        ).slice(0, 3)) {
          results.push({
            id: `task-${task.id}`,
            label: task.name || task.id,
            description: `Task · ${task.status || 'unknown'}`,
            icon: ListTodo,
            href: '/tasks',
            group: 'Tasks',
          })
        }
      } catch {}

      try {
        // Search chat history
        const chatData = await fetchChatHistory()
        const q = query.toLowerCase()
        for (const msg of (chatData.messages || []).filter(
          (m) => m.content?.toLowerCase().includes(q)
        ).slice(0, 3)) {
          results.push({
            id: `chat-${msg.id}`,
            label: msg.content?.slice(0, 60) || 'Message',
            description: `Chat · ${msg.role || 'unknown'}`,
            icon: MessageSquare,
            href: '/chat',
            group: 'Chat',
          })
        }
      } catch {}

      setDataResults(results)
      setSearching(false)
    }, 300) // debounce

    return () => clearTimeout(timer)
  }, [query])

  const allResults = [...pageResults, ...dataResults]

  const executeItem = useCallback(
    (item: PaletteItem) => {
      if (item.href) {
        router.push(item.href)
      } else if (item.action) {
        item.action()
      }
      onClose()
    },
    [router, onClose],
  )

  useEffect(() => {
    if (open) {
      setQuery('')
      setSelectedIndex(0)
      setDataResults([])
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

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

  // Scroll selected item into view
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
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 bg-black/50 backdrop-blur-sm z-[200]"
            onClick={onClose}
          />

          {/* Palette */}
          <motion.div
            initial={{ opacity: 0, scale: 0.96, y: -20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: -20 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
            className="fixed top-[20%] left-1/2 -translate-x-1/2 w-full max-w-lg z-[201]"
          >
            <div
              className="glass-card rounded-2xl overflow-hidden"
              style={{ boxShadow: '0 0 40px rgba(0, 212, 255, 0.15), 0 20px 60px rgba(0, 0, 0, 0.5)' }}
            >
              {/* Search input */}
              <div className="flex items-center gap-3 px-4 py-3 border-b border-charlie-border">
                <Search size={18} className="text-charlie-dim flex-shrink-0" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search pages, tasks, memory, chat..."
                  className="flex-1 bg-transparent text-charlie-text text-sm outline-none placeholder-charlie-dim font-body"
                />
                {searching && (
                  <div className="w-4 h-4 border-2 border-charlie-cyan/30 border-t-charlie-cyan rounded-full animate-spin" />
                )}
                <button onClick={onClose} className="text-charlie-dim hover:text-charlie-text cursor-pointer" aria-label="Close command palette">
                  <X size={16} />
                </button>
              </div>

              {/* Results */}
              <div ref={listRef} className="max-h-80 overflow-y-auto py-2">
                {allResults.length === 0 && !searching && (
                  <div className="px-4 py-8 text-center text-charlie-dim text-sm">
                    No results found
                  </div>
                )}
                {allResults.map((item, i) => {
                  const Icon = item.icon
                  const showGroup = item.group !== lastGroup
                  lastGroup = item.group
                  return (
                    <div key={item.id}>
                      {showGroup && (
                        <div className="px-4 pt-3 pb-1 text-[10px] font-semibold tracking-[0.15em] uppercase text-charlie-dim/60">
                          {item.group}
                        </div>
                      )}
                      <button
                        onClick={() => executeItem(item)}
                        onMouseEnter={() => setSelectedIndex(i)}
                        className={cn(
                          'w-full flex items-center gap-3 px-4 py-2 text-left transition-colors cursor-pointer',
                          i === selectedIndex
                            ? 'bg-charlie-cyan/10 text-charlie-text'
                            : 'text-charlie-dim hover:text-charlie-text',
                        )}
                      >
                        <Icon size={16} className="flex-shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium">{item.label}</div>
                          <div className="text-xs text-charlie-dim truncate">{item.description}</div>
                        </div>
                        {i === selectedIndex && (
                          <kbd className="text-[10px] text-charlie-dim bg-charlie-card px-1.5 py-0.5 rounded border border-charlie-border">
                            Enter
                          </kbd>
                        )}
                      </button>
                    </div>
                  )
                })}
              </div>

              {/* Footer */}
              <div className="px-4 py-2 border-t border-charlie-border flex items-center gap-4 text-[10px] text-charlie-dim">
                <span><kbd className="bg-charlie-card px-1 rounded border border-charlie-border">↑↓</kbd> Navigate</span>
                <span><kbd className="bg-charlie-card px-1 rounded border border-charlie-border">Enter</kbd> Select</span>
                <span><kbd className="bg-charlie-card px-1 rounded border border-charlie-border">Esc</kbd> Close</span>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
