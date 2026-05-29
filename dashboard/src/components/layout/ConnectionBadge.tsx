'use client'

import { useDashboardStore } from '@/lib/store'
import { cn } from '@/lib/utils'

export function ConnectionBadge() {
  const status = useDashboardStore((s) => s.connectionStatus)

  const config = {
    connected: { label: 'Connected', dot: 'bg-charlie-green', shadow: 'shadow-charlie-green/50' },
    disconnected: { label: 'Disconnected', dot: 'bg-charlie-red', shadow: 'shadow-charlie-red/50' },
    reconnecting: { label: 'Reconnecting', dot: 'bg-charlie-amber', shadow: 'shadow-charlie-amber/50' },
  }[status]

  return (
    <div className="flex items-center gap-2">
      <span className={cn('w-2 h-2 rounded-full', config.dot, `shadow-[0_0_6px]`, config.shadow)} />
      <span className="text-charlie-dim text-xs">{config.label}</span>
    </div>
  )
}
