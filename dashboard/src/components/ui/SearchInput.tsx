'use client'

import { cn } from '@/lib/utils'

interface SearchInputProps {
  value: string
  onChange: (v: string) => void
  onSearch?: () => void
  placeholder?: string
  className?: string
}

export function SearchInput({ value, onChange, onSearch, placeholder = 'Search...', className }: SearchInputProps) {
  return (
    <div className={cn('relative', className)}>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && onSearch?.()}
        placeholder={placeholder}
        className="w-full bg-charlie-card border border-charlie-border rounded-lg px-4 py-2 pl-10 text-sm text-charlie-text placeholder-charlie-dim focus:outline-none focus:border-charlie-cyan/50 transition-colors"
      />
      <svg
        className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-charlie-dim"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
      >
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
      </svg>
    </div>
  )
}
