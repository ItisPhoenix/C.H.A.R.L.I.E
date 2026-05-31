'use client'

import { cn } from '@/lib/utils'

interface ProgressBarProps {
  value: number
  max: number
  color?: 'cyan' | 'green' | 'amber' | 'red'
  className?: string
}

const colorMap = {
  cyan: 'bg-charlie-cyan',
  green: 'bg-charlie-green',
  amber: 'bg-charlie-amber',
  red: 'bg-charlie-red',
}

export function ProgressBar({ value, max, color = 'cyan', className }: ProgressBarProps) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100))
  return (
    <div className={cn('w-full h-1.5 bg-charlie-border rounded-full overflow-hidden', className)}>
      <div
        className={cn('h-full rounded-full transition-all duration-300 ease-out', colorMap[color])}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}
