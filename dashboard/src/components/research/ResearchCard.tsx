'use client'

import { GlassCard } from '@/components/ui/GlassCard'
import { Button } from '@/components/ui/Button'
import type { ResearchResult } from './ResearchPanel'

interface ResearchCardProps {
  result: ResearchResult
  onViewFull: () => void
  onSuggestionClick: (suggestion: string) => void
  suggestions?: string[]
}

export function ResearchCard({ result, onViewFull, onSuggestionClick, suggestions }: ResearchCardProps) {
  const topFindings = result.findings.slice(0, 3)

  return (
    <GlassCard className="!p-3 border-charlie-cyan/20 max-w-[75%]">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-charlie-cyan text-sm">&#x1F50D;</span>
        <span className="text-charlie-cyan text-xs font-medium uppercase tracking-wider">
          Research Complete
        </span>
      </div>

      <p className="text-charlie-text text-sm font-medium mb-2">{result.topic}</p>

      {topFindings.length > 0 && (
        <ul className="space-y-1 mb-3">
          {topFindings.map((f, i) => (
            <li key={i} className="flex gap-1.5 text-xs text-charlie-dim">
              <span className="text-charlie-cyan shrink-0">&#x2022;</span>
              <span>{f}</span>
            </li>
          ))}
          {result.findings.length > 3 && (
            <li className="text-xs text-charlie-dim italic">
              +{result.findings.length - 3} more findings...
            </li>
          )}
        </ul>
      )}

      {result.sources.length > 0 && (
        <p className="text-xs text-charlie-dim mb-3">
          {result.sources.length} source{result.sources.length !== 1 ? 's' : ''} found
        </p>
      )}

      {/* Quick suggestions */}
      {suggestions && suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {suggestions.slice(0, 3).map((s, i) => (
            <button
              key={i}
              onClick={() => onSuggestionClick(s)}
              className="text-xs px-2 py-1 rounded-full border border-charlie-cyan/20 text-charlie-cyan/80 hover:bg-charlie-cyan/10 transition-colors cursor-pointer"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      <Button variant="ghost" size="sm" onClick={onViewFull}>
        View Full Research
      </Button>
    </GlassCard>
  )
}
