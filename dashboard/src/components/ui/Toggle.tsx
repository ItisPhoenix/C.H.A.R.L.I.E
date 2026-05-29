'use client'

import { cn } from '@/lib/utils'

interface ToggleProps {
  enabled: boolean
  onChange: (enabled: boolean) => void
  label?: string
  className?: string
}

export function Toggle({ enabled, onChange, label, className }: ToggleProps) {
  return (
    <button
      onClick={() => onChange(!enabled)}
      className={cn('flex items-center gap-2 cursor-pointer', className)}
    >
      <div
        className={cn(
          'w-9 h-5 rounded-full transition-colors duration-200 relative',
          enabled ? 'bg-charlie-cyan/30' : 'bg-charlie-border',
        )}
      >
        <div
          className={cn(
            'absolute top-0.5 w-4 h-4 rounded-full transition-all duration-200',
            enabled ? 'left-4.5 bg-charlie-cyan' : 'left-0.5 bg-charlie-dim',
          )}
        />
      </div>
      {label && <span className="text-sm text-charlie-dim">{label}</span>}
    </button>
  )
}
