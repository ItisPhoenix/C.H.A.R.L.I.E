'use client'

import { useEffect, useRef, useState } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'

export interface ResearchResult {
  topic: string
  findings: string[]
  sources: Array<{ title: string; url: string }>
  summary: string
}

export interface ResearchFollowup {
  questions: string[]
  suggestions: string[]
  clarifying_question: string
}

interface ResearchPanelProps {
  result: ResearchResult | null
  followup: ResearchFollowup | null
  open: boolean
  onClose: () => void
  onSuggestionClick: (suggestion: string) => void
  onQuestionAnswer: (answer: string) => void
}

export function ResearchPanel({
  result,
  followup,
  open,
  onClose,
  onSuggestionClick,
  onQuestionAnswer,
}: ResearchPanelProps) {
  const cardRef = useRef<HTMLDivElement>(null)
  const [clarifyingAnswer, setClarifyingAnswer] = useState('')

  // Close on Escape
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && open) onClose()
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [open, onClose])

  if (!open || !result) return null

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm animate-fade-in"
        onClick={onClose}
      />

      {/* Centered popup card */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div
          ref={cardRef}
          className="pointer-events-auto w-full max-w-xl max-h-[85vh] flex flex-col border border-charlie-cyan/20 bg-charlie-dark/95 backdrop-blur-xl rounded-2xl shadow-2xl shadow-charlie-cyan/10 animate-slide-in overflow-hidden"
        >
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-3 border-b border-charlie-border shrink-0">
            <div className="flex items-center gap-2">
              <span className="text-charlie-cyan">&#x1F50D;</span>
              <h2 className="text-charlie-text font-semibold text-sm truncate">
                {result.topic}
              </h2>
            </div>
            <button
              onClick={onClose}
              className="text-charlie-dim hover:text-charlie-text transition-colors p-1 rounded hover:bg-charlie-card cursor-pointer"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* Scrollable content */}
          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
            {/* Summary */}
            <p className="text-charlie-text text-sm leading-relaxed">
              {result.summary}
            </p>

            {/* Key Findings */}
            {result.findings.length > 0 && (
              <section>
                <h3 className="text-charlie-cyan text-xs font-medium uppercase tracking-wider mb-2">
                  Key Findings
                </h3>
                <ul className="space-y-1.5">
                  {result.findings.map((finding, i) => (
                    <li key={i} className="flex gap-2 text-sm">
                      <span className="text-charlie-cyan mt-0.5 shrink-0">&#x2022;</span>
                      <span className="text-charlie-text">{finding}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* Sources */}
            {result.sources.length > 0 && (
              <section>
                <h3 className="text-charlie-cyan text-xs font-medium uppercase tracking-wider mb-2">
                  Sources
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {result.sources.map((source, i) => (
                    <a
                      key={i}
                      href={source.url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <Badge variant="cyan" className="hover:bg-charlie-cyan/30 transition-colors cursor-pointer">
                        {source.title}
                      </Badge>
                    </a>
                  ))}
                </div>
              </section>
            )}

            {/* Follow-up Questions */}
            {followup && followup.questions.length > 0 && (
              <section>
                <h3 className="text-charlie-cyan text-xs font-medium uppercase tracking-wider mb-2">
                  Follow-up Questions
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {followup.questions.map((q, i) => (
                    <button
                      key={i}
                      onClick={() => onSuggestionClick(q)}
                      className="text-xs px-3 py-1.5 rounded-full border border-charlie-cyan/30 text-charlie-cyan bg-charlie-cyan/5 hover:bg-charlie-cyan/15 transition-colors cursor-pointer"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </section>
            )}

            {/* Suggestions */}
            {followup && followup.suggestions.length > 0 && (
              <section>
                <h3 className="text-charlie-cyan text-xs font-medium uppercase tracking-wider mb-2">
                  Suggestions
                </h3>
                <div className="space-y-1.5">
                  {followup.suggestions.map((s, i) => (
                    <button
                      key={i}
                      onClick={() => onSuggestionClick(s)}
                      className="w-full text-left text-sm px-3 py-2 rounded-lg border border-charlie-border text-charlie-text bg-charlie-card hover:border-charlie-cyan/30 transition-colors cursor-pointer"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </section>
            )}

            {/* Clarifying Question */}
            {followup?.clarifying_question && (
              <GlassCard className="!p-3 border-charlie-amber/30">
                <p className="text-charlie-amber text-xs font-medium mb-1.5">Clarifying Question</p>
                <p className="text-charlie-text text-sm mb-3">{followup.clarifying_question}</p>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={clarifyingAnswer}
                    onChange={(e) => setClarifyingAnswer(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && clarifyingAnswer.trim()) {
                        onQuestionAnswer(clarifyingAnswer.trim())
                        setClarifyingAnswer('')
                      }
                    }}
                    placeholder="Your answer..."
                    className="flex-1 bg-charlie-dark border border-charlie-border rounded px-2 py-1 text-sm text-charlie-text placeholder-charlie-dim focus:outline-none focus:border-charlie-cyan/50"
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={!clarifyingAnswer.trim()}
                    onClick={() => {
                      if (clarifyingAnswer.trim()) {
                        onQuestionAnswer(clarifyingAnswer.trim())
                        setClarifyingAnswer('')
                      }
                    }}
                  >
                    Send
                  </Button>
                </div>
              </GlassCard>
            )}
          </div>
        </div>
      </div>
    </>
  )
}
