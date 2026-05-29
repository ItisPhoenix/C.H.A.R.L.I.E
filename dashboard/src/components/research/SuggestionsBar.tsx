'use client'

interface SuggestionsBarProps {
  suggestions: string[]
  onSuggestionClick: (suggestion: string) => void
}

export function SuggestionsBar({ suggestions, onSuggestionClick }: SuggestionsBarProps) {
  if (!suggestions || suggestions.length === 0) return null

  return (
    <div className="flex flex-wrap gap-2">
      {suggestions.map((s, i) => (
        <button
          key={i}
          onClick={() => onSuggestionClick(s)}
          className="text-xs px-3 py-1.5 rounded-full border border-charlie-cyan/30 text-charlie-cyan bg-charlie-cyan/5 hover:bg-charlie-cyan/15 transition-colors cursor-pointer"
        >
          {s}
        </button>
      ))}
    </div>
  )
}
