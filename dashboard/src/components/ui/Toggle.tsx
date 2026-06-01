'use client'

import { cn } from '@/lib/utils'

interface ToggleProps {
  enabled: boolean
  onChange: (enabled: boolean) => void
  label?: string
  'aria-label'?: string
  className?: string
  size?: 'sm' | 'md'
}

export function Toggle({ enabled, onChange, label, className, 'aria-label': ariaLabel, size = 'md' }: ToggleProps) {
  const trackSize = size === 'sm' ? 'w-8 h-5' : 'w-11 h-6'
  const thumbSize = size === 'sm' ? 'w-3.5 h-3.5' : 'w-5 h-5'
  const thumbPos = size === 'sm'
    ? (enabled ? 'left-[calc(100%-16px)]' : 'left-0.5')
    : (enabled ? 'left-[calc(100%-22px)]' : 'left-0.5')

  return (
    <button
      role="switch"
      aria-checked={enabled}
      aria-label={ariaLabel || label || 'Toggle'}
      onClick={() => onChange(!enabled)}
      className={cn('flex items-center gap-2 cursor-pointer p-1 -m-1 min-w-[44px] min-h-[44px]', className)}
    >
      <div
        className={cn(
          `${trackSize} rounded-full transition-all duration-300 relative`,
          enabled
            ? 'bg-charlie-cyan shadow-[0_0_8px_color-mix(in_srgb,var(--charlie-cyan)_30%,transparent)]'
            : 'bg-charlie-dim/30',
        )}
      >
        <div
          className={cn(
            `absolute top-0.5 ${thumbSize} ${thumbPos} rounded-full transition-all duration-300 shadow-sm`,
            enabled ? 'bg-charlie-dark' : 'bg-charlie-dim',
          )}
        />
      </div>
      {label && <span className="text-sm text-charlie-dim">{label}</span>}
    </button>
  )
}
