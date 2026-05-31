'use client'

import { useState, useCallback } from 'react'
import { PageHeader } from '@/components/layout/PageHeader'
import { GlassCard } from '@/components/ui/GlassCard'
import { SearchInput } from '@/components/ui/SearchInput'
import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { Search, MessageSquare, Brain, Terminal, ListTodo } from 'lucide-react'
import { searchAll } from '@/lib/api'

interface SearchResult {
  id: string
  source: 'chat' | 'memory' | 'tools' | 'tasks'
  title: string
  snippet: string
  relevance: number
  timestamp: string
}

const sourceIcons = {
  chat: MessageSquare,
  memory: Brain,
  tools: Terminal,
  tasks: ListTodo,
}

const sourceColors = {
  chat: 'cyan' as const,
  memory: 'purple' as const,
  tools: 'green' as const,
  tasks: 'amber' as const,
}

export default function SearchPage() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [activeSource, setActiveSource] = useState<string | null>(null)

  const handleSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([])
      return
    }
    setLoading(true)
    try {
      const data = await searchAll(q)
      const mapped = (data.results || []).map((r, i) => ({
        id: `result-${i}`,
        source: r.source || 'memory',
        title: r.category || r.source || 'Memory',
        snippet: r.content || '',
        relevance: 0,
        timestamp: r.timestamp ? new Date(r.timestamp * 1000).toLocaleString() : '',
      }))
      setResults(mapped)
    } catch (e) {
      console.error('Failed to search:', e)
      setResults([])
    } finally {
      setLoading(false)
    }
  }, [])

  const filteredResults = activeSource
    ? results.filter((r) => r.source === activeSource)
    : results

  const sourceCounts = results.reduce(
    (acc, r) => {
      acc[r.source] = (acc[r.source] || 0) + 1
      return acc
    },
    {} as Record<string, number>,
  )

  return (
    <div>
      <PageHeader title="Search" subtitle="Unified search across chat, memory, tools, and tasks" />

      <div className="mb-6">
        <SearchInput
          value={query}
          onChange={setQuery}
          onSearch={() => handleSearch(query)}
          placeholder="Search everything..."
        />
      </div>

      {/* Source filters */}
      {results.length > 0 && (
        <div className="flex gap-2 mb-4">
          <FilterTab
            label="All"
            count={results.length}
            active={activeSource === null}
            onClick={() => setActiveSource(null)}
          />
          {(['chat', 'memory', 'tools', 'tasks'] as const).map((source) => (
            <FilterTab
              key={source}
              label={source.charAt(0).toUpperCase() + source.slice(1)}
              count={sourceCounts[source] || 0}
              active={activeSource === source}
              onClick={() => setActiveSource(source)}
            />
          ))}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <LoadingSpinner label="Searching..." />
        </div>
      ) : filteredResults.length > 0 ? (
        <div className="space-y-3">
          {filteredResults.map((result) => {
            const Icon = sourceIcons[result.source]
            return (
              <GlassCard key={result.id} className="p-4 hover:border-charlie-cyan/30 cursor-pointer">
                <div className="flex items-start gap-3">
                  <div className="mt-0.5">
                    <Icon size={18} className="text-charlie-dim" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-semibold text-charlie-text text-sm">{result.title}</span>
                      <Badge variant={sourceColors[result.source]}>{result.source}</Badge>
                      <span className="text-charlie-dim text-xs ml-auto">{result.timestamp}</span>
                    </div>
                    <p className="text-charlie-dim text-sm line-clamp-2">{result.snippet}</p>
                  </div>
                </div>
              </GlassCard>
            )
          })}
        </div>
      ) : query ? (
        <EmptyState
          icon={<Search size={32} />}
          title={`No results for "${query}"`}
          description="Try different keywords or check another source"
        />
      ) : (
        <EmptyState
          icon={<Search size={32} />}
          title="Search across everything"
          description="Type to search chat history, memory, tools, and tasks"
          terminal
        />
      )}
    </div>
  )
}

function FilterTab({
  label,
  count,
  active,
  onClick,
}: {
  label: string
  count: number
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 rounded-lg text-sm transition-colors cursor-pointer ${
        active
          ? 'bg-charlie-cyan/15 text-charlie-cyan border border-charlie-cyan/30'
          : 'bg-charlie-card/50 text-charlie-dim border border-charlie-border hover:text-charlie-text'
      }`}
    >
      {label}
      {count > 0 && (
        <span className="ml-1.5 text-xs opacity-70">({count})</span>
      )}
    </button>
  )
}
