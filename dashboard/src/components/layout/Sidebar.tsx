'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useDashboardStore } from '@/lib/store'
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
  MicOff,
  Search,
  BarChart3,
  Sparkles,
  History,
  ScrollText,
  Sun,
  Moon,
  Bell,
} from 'lucide-react'

import type { LucideIcon } from 'lucide-react'
import { NotificationCenter } from '@/components/notifications/NotificationCenter'

interface NavItem {
  href: string
  label: string
  icon: LucideIcon
}

interface NavGroup {
  label: string
  items: NavItem[]
}

const navGroups: NavGroup[] = [
  {
    label: 'Core',
    items: [
      { href: '/', label: 'Voice', icon: Mic },
      { href: '/status', label: 'Status', icon: Activity },
      { href: '/chat', label: 'Chat', icon: MessageSquare },
      { href: '/tasks', label: 'Tasks', icon: ListTodo },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      { href: '/memory', label: 'Memory', icon: Brain },
      { href: '/briefing', label: 'Briefing', icon: FileText },
      { href: '/agents', label: 'Agents', icon: Users },
      { href: '/search', label: 'Search', icon: Search },
    ],
  },
  {
    label: 'Systems',
    items: [
      { href: '/tools', label: 'Tools', icon: Terminal },
      { href: '/mcp', label: 'MCP', icon: Server },
      { href: '/integrations', label: 'Integrations', icon: Puzzle },
      { href: '/automation', label: 'Automation', icon: Zap },
      { href: '/skills', label: 'Skills', icon: Sparkles },
      { href: '/evolution', label: 'Evolution', icon: History },
    ],
  },
  {
    label: 'Control',
    items: [
      { href: '/approvals', label: 'Approvals', icon: Shield },
      { href: '/settings', label: 'Settings', icon: Settings },
      { href: '/globe', label: 'Globe', icon: Globe },
      { href: '/logs', label: 'Logs', icon: ScrollText },
      { href: '/analytics', label: 'Analytics', icon: BarChart3 },
    ],
  },
]

// Persistent Voice Transcript — Last 3 lines
function SidebarTranscript() {
  const voice = useDashboardStore((s) => s.voiceActivity)
  const transcript = voice?.current_transcript || ''

  // Show last 3 lines of transcript
  const lines = transcript.split('\n').filter(Boolean).slice(-3)

  if (lines.length === 0) return null

  return (
    <div className="px-3 py-2 border-b border-charlie-border/30">
      {lines.map((line, i) => (
        <div
          key={i}
          className={cn(
            'text-[10px] font-body truncate',
            i === lines.length - 1 ? 'text-charlie-text' : 'text-charlie-dim/60',
          )}
        >
          {line}
        </div>
      ))}
    </div>
  )
}

// Voice Orb — Always visible, state-dependent colors
function SidebarVoiceOrb({ expanded }: { expanded: boolean }) {
  const voice = useDashboardStore((s) => s.voiceActivity)
  const phase = useDashboardStore((s) => s.currentPhase)

  const isListening = voice?.is_listening ?? false
  const isSpeaking = voice?.is_speaking ?? false
  const isProcessing = phase === 'processing'

  let orbColor = '#64748B' // idle
  let glowColor = 'rgba(100, 116, 139, 0.2)'
  let label = 'Idle'

  if (isSpeaking) {
    orbColor = '#22C55E'
    glowColor = 'rgba(34, 197, 94, 0.4)'
    label = 'Speaking'
  } else if (isProcessing) {
    orbColor = '#F59E0B'
    glowColor = 'rgba(245, 158, 11, 0.4)'
    label = 'Processing'
  } else if (isListening) {
    orbColor = '#00D4FF'
    glowColor = 'rgba(0, 212, 255, 0.4)'
    label = 'Listening'
  }

  const isActive = isListening || isSpeaking || isProcessing

  return (
    <div className="flex items-center justify-center py-3 border-b border-charlie-border/50">
      <Link href="/" className="group flex items-center gap-2">
        <div
          className={`${expanded ? 'w-10 h-10' : 'w-6 h-6'} rounded-full transition-all duration-300 flex items-center justify-center`}
          style={{
            background: `radial-gradient(circle, ${orbColor}40, transparent)`,
            boxShadow: isActive ? `0 0 15px ${glowColor}, 0 0 30px ${glowColor}` : 'none',
            border: `1.5px solid ${orbColor}60`,
            animation: isActive
              ? isSpeaking
                ? 'voicePulse 1s ease-in-out infinite'
                : isProcessing
                  ? 'voicePulse 0.6s ease-in-out infinite'
                  : 'voicePulse 2s ease-in-out infinite'
              : 'none',
          }}
        >
          {isActive ? (
            <Mic size={expanded ? 16 : 12} style={{ color: orbColor }} />
          ) : (
            <MicOff size={expanded ? 16 : 12} className="text-charlie-dim" />
          )}
        </div>
        {expanded && (
          <span className="font-display text-[10px] tracking-[0.1em] uppercase" style={{ color: orbColor }}>
            {label}
          </span>
        )}
      </Link>
    </div>
  )
}

export function Sidebar() {
  const pathname = usePathname()
  const collapsed = useDashboardStore((s) => s.sidebarCollapsed)
  const hovered = useDashboardStore((s) => s.sidebarHovered)
  const setHovered = useDashboardStore((s) => s.setSidebarHovered)
  const pendingApprovals = useDashboardStore((s) => s.pendingApprovals)
  const theme = useDashboardStore((s) => s.theme)
  const toggleTheme = useDashboardStore((s) => s.toggleTheme)

  const isExpanded = !collapsed || hovered

  return (
    <aside
      className={cn(
        'fixed left-0 top-0 bottom-0 z-50 flex flex-col',
        'bg-charlie-card/50 backdrop-blur-md border-r border-charlie-border',
        'transition-all duration-300 ease-out',
        isExpanded ? 'w-56' : 'w-14',
      )}
      onMouseEnter={() => collapsed && setHovered(true)}
      onMouseLeave={() => collapsed && setHovered(false)}
    >
      {/* Logo + Voice Orb */}
      <div className="h-14 flex items-center px-4 border-b border-charlie-border flex-shrink-0">
        <div className="flex items-center gap-2 overflow-hidden">
          <div className="w-2.5 h-2.5 rounded-full bg-charlie-cyan animate-pulse flex-shrink-0 shadow-neon-cyan-sm" />
          {isExpanded && (
            <span className="font-display text-charlie-cyan font-bold text-sm tracking-[0.2em] neon-text whitespace-nowrap">
              CHARLIE
            </span>
          )}
        </div>
      </div>

      {/* Voice Orb Indicator — State-dependent colors */}
      <SidebarVoiceOrb expanded={isExpanded} />

      {/* Persistent Voice Transcript */}
      {isExpanded && <SidebarTranscript />}

      {/* Nav groups */}
      <nav className="flex-1 py-2 overflow-y-auto overflow-x-hidden">
        {navGroups.map((group) => (
          <div key={group.label} className="mb-1">
            {isExpanded && (
              <div className="px-4 pt-3 pb-1 text-[10px] font-semibold tracking-[0.15em] uppercase text-charlie-dim/60">
                {group.label}
              </div>
            )}
            {!isExpanded && <div className="my-1 mx-3 border-t border-charlie-border/50" />}
            {group.items.map((item) => {
              const isActive = pathname === item.href
              const Icon = item.icon
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    'flex items-center gap-3 mx-2 rounded-lg text-sm transition-all duration-200 relative group',
                    isExpanded ? 'px-3 py-2' : 'px-0 py-2 justify-center',
                    isActive
                      ? 'bg-charlie-cyan/10 text-charlie-cyan'
                      : 'text-charlie-dim hover:text-charlie-text hover:bg-charlie-card',
                  )}
                  title={!isExpanded ? item.label : undefined}
                  aria-label={item.label}
                >
                  {isActive && (
                    <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-charlie-cyan rounded-r shadow-neon-cyan-sm" />
                  )}
                  <Icon
                    size={18}
                    className={cn(
                      'flex-shrink-0 transition-all duration-200',
                      isActive && 'drop-shadow-[0_0_6px_rgba(0,212,255,0.5)]',
                    )}
                  />
                  {isExpanded && <span className="whitespace-nowrap">{item.label}</span>}
                  {item.href === '/approvals' && pendingApprovals > 0 && (
                    <span
                      className={cn(
                        'bg-charlie-red text-white text-[10px] w-5 h-5 rounded-full flex items-center justify-center font-semibold',
                        isExpanded ? 'absolute right-3' : 'absolute -top-1 -right-1 w-4 h-4 text-[9px]',
                      )}
                    >
                      {pendingApprovals}
                    </span>
                  )}
                </Link>
              )
            })}
          </div>
        ))}
      </nav>

      {/* Bottom actions */}
      <div className="border-t border-charlie-border flex-shrink-0">
        {/* Notification bell */}
        <div
          className={cn(
            'w-full flex items-center gap-3 px-4 py-2.5 text-charlie-dim hover:text-charlie-text hover:bg-charlie-card transition-colors',
            !isExpanded && 'justify-center px-0',
          )}
        >
          <NotificationCenter />
          {isExpanded && <span className="text-sm">Notifications</span>}
        </div>

        {/* Theme toggle */}
        <button
          onClick={toggleTheme}
          className={cn(
            'w-full flex items-center gap-3 px-4 py-2.5 text-charlie-dim hover:text-charlie-text hover:bg-charlie-card transition-colors cursor-pointer',
            !isExpanded && 'justify-center px-0',
          )}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
          {isExpanded && (
            <span className="text-sm">{theme === 'dark' ? 'Light Mode' : 'Dark Mode'}</span>
          )}
        </button>
      </div>
    </aside>
  )
}
