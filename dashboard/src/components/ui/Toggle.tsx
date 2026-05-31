'use client'

import { cn } from '@/lib/utils'

interface ToggleProps {
  enabled: boolean
  onChange: (enabled: boolean) => void
  label?: string
  'aria-label'?: string
  className?: string
}

export function Toggle({ enabled, onChange, label, className, 'aria-label': ariaLabel }: ToggleProps) {
  return (
    <button
      role="switch"
      aria-checked={enabled}
      aria-label={ariaLabel || label || 'Toggle'}
      onClick={() => onChange(!enabled)}
      className={cn('flex items-center gap-2 cursor-pointer p-1.5 -m-1.5 min-w-[44px] min-h-[44px]', className)}
    >
      <div
        className={cn(
          'w-10 h-6 rounded-full transition-all duration-300 relative border border-transparent',
          enabled ? 'bg-charlie-text' : 'bg-charlie-dim border-charlie-border',
        )}
      >
        <div
          className={cn(
            'absolute top-0.5 w-4.5 h-4.5 rounded-full transition-all duration-300 shadow-sm',
            enabled ? 'left-5 bg-charlie-dark' : 'left-0.5 bg-charlie-border',
          )}
        />
      </div>
      {label && <span className="text-sm text-charlie-dim">{label}</span>}
    </button>
  )
}
