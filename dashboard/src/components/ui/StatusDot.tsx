'use client'

import { cn } from '@/lib/utils'

type Status = 'online' | 'warning' | 'error' | 'idle'

interface StatusDotProps {
  status: Status
  pulse?: boolean
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const sizeMap = {
  sm: 'w-1.5 h-1.5',
  md: 'w-2.5 h-2.5',
  lg: 'w-3 h-3',
}

const statusLabel: Record<Status, string> = {
  online: 'Status: online',
  warning: 'Status: warning',
  error: 'Status: error',
  idle: 'Status: idle',
}

export function StatusDot({ status, pulse, size = 'md', className }: StatusDotProps) {
  const shadowMap: Record<Status, string> = {
    online: '0 0 8px color-mix(in srgb, var(--charlie-green) 40%, transparent)',
    warning: '0 0 8px color-mix(in srgb, var(--charlie-amber) 40%, transparent)',
    error: '0 0 8px color-mix(in srgb, var(--charlie-red) 40%, transparent)',
    idle: '',
  }

  const colorMap: Record<Status, string> = {
    online: 'bg-charlie-green',
    warning: 'bg-charlie-amber',
    error: 'bg-charlie-red',
    idle: 'bg-charlie-dim',
  }

  return (
    <span
      className={cn(
        'rounded-full inline-block flex-shrink-0',
        sizeMap[size],
        colorMap[status],
        pulse && 'animate-pulse duration-1000',
        className,
      )}
      style={{ boxShadow: shadowMap[status] || undefined }}
      aria-label={statusLabel[status]}
      role="status"
    >
      <span className="sr-only">{statusLabel[status]}</span>
    </span>
  )
}
