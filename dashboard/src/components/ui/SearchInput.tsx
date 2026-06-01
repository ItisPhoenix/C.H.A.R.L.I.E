'use client'

import { cn } from '@/lib/utils'
import { Search } from 'lucide-react'

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
        className="w-full bg-charlie-dark border border-charlie-border rounded-lg px-4 py-2 pl-10 text-sm text-charlie-text placeholder-charlie-dim focus:outline-none focus:border-charlie-border transition-all duration-200 shadow-inner-light"
      />
      <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-charlie-dim" />
    </div>
  )
}
