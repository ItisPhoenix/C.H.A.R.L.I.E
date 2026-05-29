'use client'

import { cn } from '@/lib/utils'

interface ProgressBarProps {
  value: number
  max: number
  color?: 'cyan' | 'green' | 'amber' | 'red'
  className?: string
}

const colorMap = {
  cyan: 'bg-charlie-cyan shadow-[0_0_8px_rgba(0,212,255,0.3)]',
  green: 'bg-charlie-green shadow-[0_0_8px_rgba(34,197,94,0.3)]',
  amber: 'bg-charlie-amber shadow-[0_0_8px_rgba(245,158,11,0.3)]',
  red: 'bg-charlie-red shadow-[0_0_8px_rgba(239,68,68,0.3)]',
}

export function ProgressBar({ value, max, color = 'cyan', className }: ProgressBarProps) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100))
  return (
    <div className={cn('w-full h-1.5 bg-charlie-border rounded-full overflow-hidden', className)}>
      <div
        className={cn('h-full rounded-full transition-all duration-700 ease-out', colorMap[color])}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}
