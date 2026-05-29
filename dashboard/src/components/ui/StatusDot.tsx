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
  sm: 'w-2 h-2',
  md: 'w-2 h-2',
  lg: 'w-3 h-3',
}

export function StatusDot({ status, pulse, size = 'md', className }: StatusDotProps) {
  const shadowMap: Record<Status, string> = {
    online: 'shadow-[0_0_6px_rgba(34,197,94,0.5)]',
    warning: 'shadow-[0_0_6px_rgba(245,158,11,0.5)]',
    error: 'shadow-[0_0_6px_rgba(239,68,68,0.5)]',
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
        shadowMap[status],
        pulse && 'animate-pulse',
        className,
      )}
    />
  )
}
