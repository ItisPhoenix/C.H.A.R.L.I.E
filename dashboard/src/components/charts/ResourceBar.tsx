'use client'

import { cn } from '@/lib/utils'

interface ResourceBarProps {
  label: string
  value: number
  max?: number
  unit?: string
  className?: string
}

export function ResourceBar({ label, value, max = 100, unit = '%', className }: ResourceBarProps) {
  const pct = Math.min(100, (value / max) * 100)
  const color = pct > 90 ? 'bg-charlie-red' : pct > 70 ? 'bg-charlie-amber' : 'bg-charlie-cyan'

  return (
    <div className={cn('space-y-1', className)}>
      <div className="flex justify-between text-xs">
        <span className="text-charlie-dim">{label}</span>
        <span className="text-charlie-text">{value.toFixed(1)}{unit}</span>
      </div>
      <div className="h-2 bg-charlie-border rounded-full overflow-hidden">
        <div
          className={cn('h-full rounded-full transition-all duration-300', color)}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}
