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
  Plug,
  Terminal,
  Mic,
  MicOff,
  Search,
  BarChart3,
  Sparkles,
  History,
  ScrollText,
  Bell,
  TrendingUp,
  ChevronDown,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react'

import type { LucideIcon } from 'lucide-react'
import { NotificationCenter } from '@/components/notifications/NotificationCenter'

interface NavItem {
  label: string
  href: string
  icon: LucideIcon
}

interface NavGroup {
  label: string
  items: NavItem[]
}

const navGroups: NavGroup[] = [
  {
    label: 'Overview',
    items: [
      { label: 'Status', href: '/status', icon: Activity },
      { label: 'Briefing', href: '/briefing', icon: Sparkles },
      { label: 'Analytics', href: '/analytics', icon: BarChart3 },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      { label: 'Agents', href: '/agents', icon: Brain },
      { label: 'Knowledge', href: '/memory', icon: History },
      { label: 'Evolution', href: '/evolution', icon: TrendingUp },
    ],
  },
  {
    label: 'Operations',
    items: [
      { label: 'Tasks', href: '/tasks', icon: ListTodo },
      { label: 'Approvals', href: '/approvals', icon: Shield },
      { label: 'Automation', href: '/automation', icon: Zap },
    ],
  },
  {
    label: 'Ecosystem',
    items: [
      { label: 'MCP Servers', href: '/mcp', icon: Server },
      { label: 'Skills', href: '/skills', icon: Puzzle },
      { label: 'Integrations', href: '/integrations', icon: Plug },
      { label: 'Tools', href: '/tools', icon: Terminal },
    ],
  },
  {
    label: 'Interface',
    items: [
      { label: 'Conversation', href: '/chat', icon: MessageSquare },
      { label: 'Search', href: '/search', icon: Search },
      { label: 'Voice Control', href: '/voice', icon: Mic },
      { label: 'Logs', href: '/logs', icon: ScrollText },
      { label: 'Settings', href: '/settings', icon: Settings },
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
    <div className="px-3 py-3 border-b border-charlie-border/20 bg-charlie-cyan/5">
      {lines.map((line, i) => (
        <div
          key={i}
          className={cn(
            'text-xs font-body truncate',
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

  let orbColorVar = 'var(--voice-idle)'
  let orbGlowOpacity = 0.2
  let label = 'Idle'

  if (isSpeaking) {
    orbColorVar = 'var(--voice-speaking)'
    orbGlowOpacity = 0.4
    label = 'Speaking'
  } else if (isProcessing) {
    orbColorVar = 'var(--voice-processing)'
    orbGlowOpacity = 0.4
    label = 'Processing'
  } else if (isListening) {
    orbColorVar = 'var(--voice-listening)'
    orbGlowOpacity = 0.4
    label = 'Listening'
  }

  const isActive = isListening || isSpeaking || isProcessing

  return (
    <div className="flex items-center justify-center py-4 border-b border-charlie-border/20">
      <Link href="/" className="group flex items-center gap-2">
        <div
          className={`${expanded ? 'w-10 h-10' : 'w-6 h-6'} rounded-full transition-all duration-300 flex items-center justify-center`}
          style={{
            background: `radial-gradient(circle at 30% 30%, color-mix(in srgb, ${orbColorVar} 50%, transparent), color-mix(in srgb, ${orbColorVar} 12%, transparent))`,
            boxShadow: isActive ? `0 4px 15px color-mix(in srgb, ${orbColorVar} ${orbGlowOpacity * 100}%, transparent), 0 0 30px color-mix(in srgb, ${orbColorVar} ${orbGlowOpacity * 100}%, transparent)` : 'none',
            border: `1px solid color-mix(in srgb, ${orbColorVar} 50%, transparent)`,
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
            <Mic size={expanded ? 16 : 12} style={{ color: orbColorVar }} />
          ) : (
            <MicOff size={expanded ? 16 : 12} className="text-charlie-dim" />
          )}
        </div>
        {expanded && (
          <span className="font-sans text-xs tracking-[0.1em] uppercase font-semibold" style={{ color: orbColorVar }}>
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
  const pendingApprovals = useDashboardStore((s) => s.pendingApprovals)

  const isExpanded = !collapsed
  const toggleSidebar = useDashboardStore((s) => s.toggleSidebar)

  return (
    <aside
      className={cn(
        'fixed left-0 top-0 bottom-0 z-50 flex flex-col',
        'bg-charlie-dark/80 backdrop-blur-md border-r border-charlie-border',
        'transition-all duration-300 ease-out',
        isExpanded ? 'w-56' : 'w-14',
      )}
    >
      {/* Logo + Toggle */}
      <div className="h-14 flex items-center px-4 border-b border-charlie-border flex-shrink-0">
        <div className="flex items-center gap-2 overflow-hidden flex-1">
          <div className="w-2 h-2 bg-charlie-text flex-shrink-0" />
          {isExpanded && (
            <span className="font-sans text-charlie-text font-semibold text-sm tracking-widest whitespace-nowrap">
              CHARLIE
            </span>
          )}
        </div>
        <button
          onClick={toggleSidebar}
          aria-label={isExpanded ? 'Collapse sidebar' : 'Expand sidebar'}
          className="text-charlie-dim hover:text-charlie-text cursor-pointer transition-colors p-1"
        >
          {isExpanded ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
        </button>
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
              <div className="px-4 pt-3 pb-1 text-xs font-semibold tracking-[0.15em] uppercase text-charlie-dim">
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
                    'flex items-center gap-3 mx-2 rounded-md text-sm transition-all duration-200 relative group',
                    isExpanded ? 'px-3 py-2' : 'px-0 py-2 justify-center',
                    isActive
                      ? 'bg-charlie-text/10 text-charlie-text shadow-inner-light'
                      : 'text-charlie-dim hover:text-charlie-text hover:bg-charlie-text/5',
                  )}
                  title={!isExpanded ? item.label : undefined}
                  aria-label={item.label}
                >
                  {isActive && (
                    <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-4 bg-charlie-text rounded-r" />
                  )}
                  <Icon
                    size={18}
                    className="flex-shrink-0 transition-all duration-200 group-hover:scale-105"
                  />
                  {isExpanded && <span className="whitespace-nowrap">{item.label}</span>}
                  {item.href === '/approvals' && pendingApprovals > 0 && (
                    <span
                      className={cn(
                        'bg-charlie-red text-charlie-dark text-xs w-5 h-5 rounded flex items-center justify-center font-medium',
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
            'w-full flex items-center gap-3 px-4 py-2 text-charlie-dim hover:text-charlie-text hover:bg-charlie-text/5 transition-all duration-200 cursor-pointer rounded-md mx-2 w-[calc(100%-16px)] mb-1',
            !isExpanded && 'justify-center px-0 mx-0 w-full rounded-none',
          )}
        >
          <NotificationCenter />
          {isExpanded && <span className="text-sm text-inherit">Notifications</span>}
        </div>

      </div>
    </aside>
  )
}
