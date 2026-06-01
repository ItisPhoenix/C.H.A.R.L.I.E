'use client'

import { useState, useCallback } from 'react'
import { PageHeader } from '@/components/layout/PageHeader'
import { GlassCard } from '@/components/ui/GlassCard'
import { SearchInput } from '@/components/ui/SearchInput'
import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorState } from '@/components/ui/ErrorState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { FilterBar } from '@/components/ui/FilterBar'
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
  const [error, setError] = useState<string | null>(null)
  const [activeSource, setActiveSource] = useState<'all' | 'chat' | 'memory' | 'tools' | 'tasks'>('all')

  const handleSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([])
      return
    }
    setLoading(true)
    setError(null)
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
      setError('Search failed. The brain may be disconnected.')
      setResults([])
    } finally {
      setLoading(false)
    }
  }, [])

  const filteredResults = activeSource !== 'all'
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
    <div className="max-w-6xl mx-auto space-y-6">
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
        <div className="mb-4">
          <FilterBar
            options={['all', 'chat', 'memory', 'tools', 'tasks'] as const}
            value={activeSource}
            onChange={setActiveSource}
            badge={(key) => key === 'all' ? results.length : (sourceCounts[key] || 0)}
          />
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <LoadingSpinner label="Searching..." />
        </div>
      ) : error ? (
        <ErrorState error={error} onRetry={() => handleSearch(query)} />
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
