'use client'

import { cn } from '@/lib/utils'

interface FilterBarProps<T extends string> {
  options: readonly T[]
  value: T
  onChange: (v: T) => void
  label?: (v: T) => string
  badge?: (v: T) => number | undefined
  className?: string
}

export function FilterBar<T extends string>({
  options,
  value,
  onChange,
  label,
  badge,
  className,
}: FilterBarProps<T>) {
  return (
    <div className={cn('flex gap-1 bg-charlie-card rounded-lg p-0.5', className)}>
      {options.map((opt) => {
        const count = badge?.(opt)
        return (
          <button
            key={opt}
            onClick={() => onChange(opt)}
            className={cn(
              'px-3 py-1 rounded text-xs transition-colors cursor-pointer',
              value === opt
                ? 'bg-charlie-cyan/15 text-charlie-cyan'
                : 'text-charlie-dim hover:text-charlie-text hover:bg-charlie-text/5',
            )}
          >
            {label ? label(opt) : opt.charAt(0).toUpperCase() + opt.slice(1)}
            {count !== undefined && count > 0 && (
              <span className="ml-1 text-[10px] opacity-70">({count})</span>
            )}
          </button>
        )
      })}
    </div>
  )
}
