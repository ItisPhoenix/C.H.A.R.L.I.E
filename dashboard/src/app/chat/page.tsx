'use client'

import { useEffect, useState, useRef, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { fetchChatHistory, sendMessage } from '@/lib/api'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { StatusDot } from '@/components/ui/StatusDot'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { PageHeader } from '@/components/layout/PageHeader'
import { useWSEvent } from '@/lib/ws'
import { useDashboardStore } from '@/lib/store'
import { formatTimestamp, cn } from '@/lib/utils'
import type { ChatMessage } from '@/lib/types'
import { Mic, MicOff, Send, ChevronRight, ChevronLeft, Brain, Terminal, Users, Activity, Zap, Puzzle, PanelRightOpen } from 'lucide-react'
import { createVisibilityAwareInterval } from '@/lib/utils'

// ==================== CONTEXT PANEL ====================

function ContextPanel({ collapsed, onToggle, onQuickAction, mobile }: { collapsed: boolean; onToggle: () => void; onQuickAction?: (prompt: string) => void; mobile?: boolean }) {
  const [currentTask, setCurrentTask] = useState<{ name: string; status: string } | null>(null)
  const [recentMemory, setRecentMemory] = useState<string[]>([])
  const [toolsUsed, setToolsUsed] = useState<Array<{ name: string; status: string }>>([])
  const [agents, setAgents] = useState<Array<{ name: string; status: string; task: string }>>([])
  const [integrations, setIntegrations] = useState<Array<{ name: string; connected: boolean }>>([])
  const [sessionInfo, setSessionInfo] = useState({ messages: 0, duration: '0m', model: 'unknown' })

  useEffect(() => {
    async function loadContext() {
      try {
        const { fetchTasks, fetchToolLog, fetchAgentStatus, fetchIntegrations, fetchChatHistory } = await import('@/lib/api')
        const [tasksData, toolsData, agentData, intData, chatData] = await Promise.all([
          fetchTasks().catch(() => ({ tasks: [] })),
          fetchToolLog().catch(() => ({ executions: [] })),
          fetchAgentStatus().catch(() => ({ orchestrator: { status: 'idle' }, agents: [] })),
          fetchIntegrations().catch(() => ({ integrations: [] })),
          fetchChatHistory().catch(() => ({ messages: [] })),
        ])

        const active = (tasksData.tasks || []).find((t: { status: string }) => t.status === 'active' || t.status === 'running')
        setCurrentTask(active ? { name: active.name || active.id, status: active.status } : null)

        setToolsUsed((toolsData.executions || []).slice(0, 5).map((e: { tool_name?: string; outcome_type?: string }) => ({
          name: e.tool_name || 'unknown',
          status: e.outcome_type || 'success',
        })))

        setAgents((agentData.agents || []).slice(0, 4).map((a: { name: string; status: string; current_task?: string }) => ({
          name: a.name,
          status: a.status,
          task: a.current_task || '—',
        })))

        setIntegrations((intData.integrations || []).map((i: { name: string; status: string }) => ({
          name: i.name,
          connected: i.status === 'connected' || i.status === 'healthy',
        })))

        const msgs = chatData.messages || []
        setSessionInfo({
          messages: msgs.length,
          duration: msgs.length > 0 ? `${Math.max(1, Math.round((Date.now() / 1000 - (msgs[0]?.timestamp || Date.now() / 1000)) / 60))}m` : '0m',
          model: 'Active',
        })
      } catch (e) {
        console.error('Failed to load context:', e)
      }
    }
    loadContext()
    return createVisibilityAwareInterval(loadContext, 15000)
  }, [])

  if (collapsed && !mobile) {
    return (
      <button
        onClick={onToggle}
        className="hidden lg:flex w-10 items-center justify-center border-l border-charlie-border hover:bg-charlie-card/50 transition-colors cursor-pointer"
        title="Show context panel"
      >
        <ChevronLeft size={16} className="text-charlie-dim" />
      </button>
    )
  }

  return (
    <div className={`w-80 border-l border-charlie-border flex flex-col overflow-hidden ${mobile ? '' : 'hidden lg:flex'}`}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-charlie-border">
        <span className="font-display text-xs tracking-[0.1em] text-charlie-cyan uppercase">Context</span>
        <button onClick={onToggle} className="text-charlie-dim hover:text-charlie-text cursor-pointer">
          <ChevronRight size={16} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        <ContextSection icon={<Zap size={14} />} title="Current Task" color="cyan">
          {currentTask ? (
            <>
              <div className="text-sm text-charlie-text font-body">{currentTask.name}</div>
              <Badge variant="cyan">{currentTask.status}</Badge>
            </>
          ) : (
            <div className="text-xs text-charlie-dim">No active tasks</div>
          )}
        </ContextSection>

        <ContextSection icon={<Brain size={14} />} title="Recent Memory" color="purple">
          {recentMemory.length > 0 ? (
            <div className="space-y-1.5">
              {recentMemory.map((item, i) => (
                <div key={i} className="text-xs text-charlie-dim font-body flex items-start gap-1.5">
                  <span className="text-charlie-purple mt-0.5">•</span>
                  {item}
                </div>
              ))}
            </div>
          ) : (
            <div className="text-xs text-charlie-dim">No recent memories</div>
          )}
        </ContextSection>

        <ContextSection icon={<Terminal size={14} />} title="Tools Used" color="green">
          {toolsUsed.length > 0 ? (
            <div className="space-y-1">
              {toolsUsed.map((tool, i) => (
                <div key={i} className="flex items-center justify-between text-xs">
                  <span className="font-mono text-charlie-text">{tool.name}</span>
                  <StatusDot status={tool.status === 'success' ? 'online' : tool.status === 'error' ? 'error' : 'warning'} />
                </div>
              ))}
            </div>
          ) : (
            <div className="text-xs text-charlie-dim">No tools used yet</div>
          )}
        </ContextSection>

        <ContextSection icon={<Users size={14} />} title="Agent Status" color="amber">
          {agents.length > 0 ? (
            <div className="space-y-1">
              {agents.map((agent, i) => (
                <div key={i} className="text-xs">
                  <div className="flex items-center justify-between">
                    <span className="font-body text-charlie-text">{agent.name}</span>
                    <Badge variant={agent.status === 'running' || agent.status === 'busy' ? 'green' : agent.status === 'error' ? 'red' : 'dim'}>{agent.status}</Badge>
                  </div>
                  <div className="text-charlie-dim mt-0.5">{agent.task}</div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-xs text-charlie-dim">No agents active</div>
          )}
        </ContextSection>

        <ContextSection icon={<Activity size={14} />} title="Session Info" color="cyan">
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div><span className="text-charlie-dim">Messages:</span> <span className="font-mono text-charlie-text">{sessionInfo.messages}</span></div>
            <div><span className="text-charlie-dim">Duration:</span> <span className="font-mono text-charlie-text">{sessionInfo.duration}</span></div>
            <div className="col-span-2"><span className="text-charlie-dim">Model:</span> <span className="font-mono text-charlie-cyan">{sessionInfo.model}</span></div>
          </div>
        </ContextSection>

        <ContextSection icon={<Puzzle size={14} />} title="Integrations" color="teal">
          {integrations.length > 0 ? (
            <div className="flex gap-3 flex-wrap">
              {integrations.map((int, i) => (
                <div key={i} className="flex items-center gap-1 text-xs">
                  <StatusDot status={int.connected ? 'online' : 'idle'} size="sm" />
                  <span className="text-charlie-dim">{int.name}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-xs text-charlie-dim">No integrations</div>
          )}
        </ContextSection>

        <ContextSection icon={<Zap size={14} />} title="Quick Actions" color="amber">
          <div className="flex flex-wrap gap-1.5">
            {[
              { label: 'Summarize', prompt: 'Summarize our conversation' },
              { label: 'Search memory', prompt: 'Search my memory for ' },
              { label: 'Create skill', prompt: 'Create a new skill that ' },
            ].map((action) => (
              <button
                key={action.label}
                onClick={() => onQuickAction?.(action.prompt)}
                className="px-2 py-1 text-xs rounded bg-charlie-cyan/10 text-charlie-cyan border border-charlie-cyan/20 hover:bg-charlie-cyan/20 transition-colors cursor-pointer"
              >
                {action.label}
              </button>
            ))}
          </div>
        </ContextSection>
      </div>
    </div>
  )
}

const colorMap: Record<string, string> = {
  cyan: 'text-charlie-cyan',
  green: 'text-charlie-green',
  amber: 'text-charlie-amber',
  purple: 'text-charlie-purple',
  teal: 'text-charlie-teal',
  orange: 'text-charlie-orange',
  red: 'text-charlie-red',
}

function ContextSection({ icon, title, color, children }: { icon: React.ReactNode; title: string; color: string; children: React.ReactNode }) {
  return (
    <GlassCard className="p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className={colorMap[color] || 'text-charlie-cyan'}>{icon}</span>
        <span className="font-display text-[10px] tracking-[0.1em] uppercase text-charlie-dim">{title}</span>
      </div>
      {children}
    </GlassCard>
  )
}

// ==================== VOICE INPUT ====================

function VoiceInput({ onTranscript }: { onTranscript?: (text: string) => void }) {
  const voice = useDashboardStore((s) => s.voiceActivity)
  const isListening = voice?.is_listening ?? false
  const [isRecording, setIsRecording] = useState(false)
  const recognitionRef = useRef<any>(null)

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.stop()
        recognitionRef.current = null
      }
    }
  }, [])

  function startPushToTalk() {
    const win = window as unknown as { SpeechRecognition: unknown; webkitSpeechRecognition: unknown }
    const SpeechRecognition = (win.SpeechRecognition || win.webkitSpeechRecognition) as { new(): unknown; prototype: unknown } | undefined
    if (!SpeechRecognition) {
      console.warn('Speech recognition not supported in this browser')
      return
    }

    const recognition = new SpeechRecognition() as {
      continuous: boolean;
      interimResults: boolean;
      lang: string;
      onresult: (event: unknown) => void;
      onend: () => void;
      onerror: () => void;
      start: () => void;
    }
    recognition.continuous = false
    recognition.interimResults = false
    recognition.lang = 'en-US'

    recognition.onresult = (event: unknown) => {
      const e = event as { results: { transcript: string }[][] }
      const transcript = e.results[0]?.[0]?.transcript
      if (transcript && onTranscript) {
        onTranscript(transcript)
      }
    }

    recognition.onend = () => {
      setIsRecording(false)
      recognitionRef.current = null
    }

    recognition.onerror = () => {
      setIsRecording(false)
      recognitionRef.current = null
    }

    recognitionRef.current = recognition
    recognition.start()
    setIsRecording(true)
  }

  function stopPushToTalk() {
    if (recognitionRef.current) {
      recognitionRef.current.stop()
      recognitionRef.current = null
    }
    setIsRecording(false)
  }

  const active = isListening || isRecording

  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b border-charlie-border bg-charlie-card/30">
      <button
        className={cn(
          'w-10 h-10 rounded-full flex items-center justify-center transition-all cursor-pointer',
          active
            ? 'bg-charlie-cyan/20 shadow-neon-cyan animate-pulse'
            : 'bg-charlie-card border border-charlie-border hover:border-charlie-cyan/30',
        )}
        title={active ? 'Listening...' : 'Click to speak'}
        aria-label={active ? 'Listening...' : 'Click to speak'}
        onClick={() => {
          if (isRecording) {
            stopPushToTalk()
          } else {
            startPushToTalk()
          }
        }}
      >
        {active ? (
          <Mic size={18} className="text-charlie-cyan" />
        ) : (
          <MicOff size={18} className="text-charlie-dim" />
        )}
      </button>
      <div className="flex-1">
        {active ? (
          <div className="flex items-center gap-2">
            <div className="flex items-end gap-0.5 h-4">
              {[0, 1, 2, 3, 4].map((i) => (
                <div
                  key={i}
                  className="w-1 bg-charlie-cyan rounded-full"
                  style={{
                    '--wave-height': `${10 + i * 3}px`,
                    animation: `waveform 0.6s ease-in-out ${i * 0.1}s infinite`,
                  } as React.CSSProperties}
                />
              ))}
            </div>
            <span className="text-charlie-cyan text-sm font-body">
              {isRecording ? 'Recording... click mic to stop' : 'Listening...'}
            </span>
          </div>
        ) : (
          <span className="text-charlie-dim text-sm font-body">Press mic to speak, or type below</span>
        )}
      </div>
    </div>
  )
}

// ==================== MESSAGE BUBBLES ====================

function ToolResultCard({ metadata }: { metadata: Record<string, unknown> }) {
  const toolName = metadata.tool_name as string | undefined
  const status = metadata.status as string | undefined
  const duration = metadata.duration_ms as number | undefined
  if (!toolName) return null

  return (
    <div className="mt-2 terminal-block">
      <div className="terminal-header">
        <div className="dot" style={{ background: status === 'success' ? '#22C55E' : status === 'error' ? '#EF4444' : '#F59E0B' }} />
        <span className="text-charlie-cyan text-xs font-mono">{toolName}</span>
        {duration !== undefined && <span className="text-charlie-dim text-xs ml-auto">{duration}ms</span>}
      </div>
      <div className="terminal-content text-xs">
        {status === 'success' ? 'Completed successfully' : status === 'error' ? 'Failed' : 'Running...'}
      </div>
    </div>
  )
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const { role, content, timestamp, metadata } = message

  if (role === 'system') {
    return (
      <div className="flex justify-center">
        <div className="text-xs text-charlie-dim bg-charlie-card/50 px-3 py-1 rounded-full font-body">
          {content}
        </div>
      </div>
    )
  }

  const isUser = role === 'user'

  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[75%] rounded-lg px-3 py-2 text-sm transition-all',
          isUser
            ? 'bg-charlie-cyan/15 text-charlie-text border border-charlie-cyan/25 shadow-neon-cyan-sm'
            : 'bg-charlie-card text-charlie-text border border-charlie-border',
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap break-words font-body">{content}</p>
        ) : (
          <div className="prose-chat">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        )}
        {metadata && <ToolResultCard metadata={metadata} />}
        <p className={cn('text-xs mt-1 font-mono', isUser ? 'text-charlie-cyan/40' : 'text-charlie-dim/60')}>
          {formatTimestamp(timestamp)}
        </p>
      </div>
    </div>
  )
}

// ==================== MAIN CHAT PAGE ====================

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [contextCollapsed, setContextCollapsed] = useState(false)
  const [showMobileContext, setShowMobileContext] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    loadHistory()
  }, [])

  const scrollToBottom = useCallback(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  const wsReply = useWSEvent<ChatMessage>('chat_reply')
  useEffect(() => {
    if (wsReply) {
      // Ensure message has an ID for dedup (backend may not send one)
      const msg = wsReply.id ? wsReply : { ...wsReply, id: `ws-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` }
      setMessages((prev) => {
        if (msg.id && prev.some((m) => m.id === msg.id)) return prev
        // Also dedup by content+role for messages without stable IDs
        if (!wsReply.id && prev.some((m) => m.role === msg.role && m.content === msg.content)) return prev
        return [...prev, msg]
      })
      setSending(false)
    }
  }, [wsReply])

  async function loadHistory() {
    setLoading(true)
    try {
      const data = await fetchChatHistory()
      setMessages(data.messages || [])
    } catch (e) {
      console.error('Failed to load chat history:', e)
      setMessages([])
    }
    setLoading(false)
  }

  async function handleSend() {
    const content = input.trim()
    if (!content || sending) return

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content,
      timestamp: Date.now() / 1000,
    }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setSending(true)

    try {
      await sendMessage(content)
    } catch (e) {
      console.error('Failed to send message:', e)
      setSending(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="flex h-[calc(100vh-8rem)]">
      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        <PageHeader
          title="Chat"
          subtitle="Voice-first conversation with CHARLIE"
          actions={
            <button
              onClick={() => setShowMobileContext(!showMobileContext)}
              className="lg:hidden flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-charlie-border text-charlie-dim hover:text-charlie-text hover:border-charlie-cyan/30 transition-colors cursor-pointer"
              title="Toggle context panel"
            >
              <PanelRightOpen size={14} />
              Context
            </button>
          }
        />

        <GlassCard className="flex-1 flex flex-col min-h-0 !p-0 overflow-hidden">
          {/* Voice input */}
          <VoiceInput onTranscript={async (text) => {
            const userMsg: ChatMessage = {
              id: `voice-${Date.now()}`,
              role: 'user',
              content: text,
              timestamp: Date.now() / 1000,
            }
            setMessages((prev) => [...prev, userMsg])
            setSending(true)
            try {
              await sendMessage(text)
            } catch (e) {
              console.error('Failed to send voice message:', e)
            }
          }} />

          {/* Message list */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <LoadingSpinner label="Loading history..." />
              </div>
            ) : messages.length === 0 ? (
              <div className="flex items-center justify-center py-12">
                <div className="text-center">
                  <Mic size={32} className="text-charlie-dim mx-auto mb-3" />
                  <p className="text-charlie-dim text-sm font-body">No messages yet. Say hello or type below.</p>
                </div>
              </div>
            ) : (
              messages.map((msg) => <MessageBubble key={msg.id} message={msg} />)
            )}

            {sending && (
              <div className="flex justify-start">
                <div className="bg-charlie-card border border-charlie-border rounded-lg px-4 py-2.5 text-sm">
                  <span className="inline-flex items-center gap-1.5 text-charlie-dim font-body">
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-charlie-cyan animate-pulse" />
                    thinking...
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Text input (fallback) */}
          <div className="border-t border-charlie-border p-3 flex gap-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type a message (or use voice above)..."
              disabled={sending}
              aria-label="Chat message input"
              className="flex-1 bg-charlie-card border border-charlie-border rounded-lg px-4 py-2 text-sm text-charlie-text placeholder-charlie-dim focus:outline-none focus:border-charlie-cyan/50 focus:shadow-neon-cyan-sm transition-all disabled:opacity-50 font-body"
            />
            <Button
              variant="primary"
              size="md"
              loading={sending}
              onClick={handleSend}
              disabled={!input.trim()}
            >
              <Send size={16} />
            </Button>
          </div>
        </GlassCard>
      </div>

      {/* Context panel */}
      <ContextPanel collapsed={contextCollapsed} onToggle={() => setContextCollapsed(!contextCollapsed)} onQuickAction={setInput} />

      {/* Mobile context overlay */}
      {showMobileContext && (
        <div className="lg:hidden fixed inset-0 z-50 flex">
          <div className="flex-1 bg-black/50" onClick={() => setShowMobileContext(false)} />
          <div className="w-80 max-w-[85vw] bg-charlie-dark border-l border-charlie-border flex flex-col overflow-hidden">
            <ContextPanel
              collapsed={false}
              onToggle={() => setShowMobileContext(false)}
              onQuickAction={(text: string) => { setInput(text); setShowMobileContext(false) }}
              mobile
            />
          </div>
        </div>
      )}
    </div>
  )
}
