'use client'

import { useState } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Button } from '@/components/ui/Button'

interface QuestionPromptProps {
  question: string
  onAnswer: (answer: string) => void
  onDismiss: () => void
}

export function QuestionPrompt({ question, onAnswer, onDismiss }: QuestionPromptProps) {
  const [answer, setAnswer] = useState('')

  function handleSubmit() {
    const trimmed = answer.trim()
    if (trimmed) {
      onAnswer(trimmed)
      setAnswer('')
    }
  }

  return (
    <GlassCard className="!p-3 border-charlie-amber/30">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-charlie-amber text-sm">?</span>
          <span className="text-charlie-amber text-xs font-medium uppercase tracking-wider">
            Clarifying Question
          </span>
        </div>
        <button
          onClick={onDismiss}
          className="text-charlie-dim hover:text-charlie-text transition-colors p-0.5 cursor-pointer"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <p className="text-charlie-text text-sm mb-3">{question}</p>

      <div className="flex gap-2">
        <input
          type="text"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSubmit()
          }}
          placeholder="Your answer..."
          className="flex-1 bg-charlie-dark border border-charlie-border rounded px-3 py-1.5 text-sm text-charlie-text placeholder-charlie-dim focus:outline-none focus:border-charlie-cyan/50 transition-colors"
        />
        <Button variant="primary" size="sm" onClick={handleSubmit} disabled={!answer.trim()}>
          Send
        </Button>
      </div>
    </GlassCard>
  )
}
