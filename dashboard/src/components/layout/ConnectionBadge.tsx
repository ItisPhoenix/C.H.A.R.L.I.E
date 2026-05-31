'use client'

import { useDashboardStore } from '@/lib/store'
import { cn } from '@/lib/utils'

export function ConnectionBadge() {
  const status = useDashboardStore((s) => s.connectionStatus)

  const config = {
    connected: { label: 'Sync Active', dot: 'bg-charlie-green', shadow: 'rgba(34,197,94,0.3)' },
    disconnected: { label: 'Link Severed', dot: 'bg-charlie-red', shadow: 'rgba(239,68,68,0.3)' },
    reconnecting: { label: 'Relinking...', dot: 'bg-charlie-amber', shadow: 'rgba(245,158,11,0.3)' },
  }[status]

  return (
    <div className="flex items-center gap-2">
      <span className={cn('w-2 h-2 rounded-full', config.dot)} style={{ boxShadow: `0 0 10px ${config.shadow}` }} />
      <span className="text-charlie-dim text-xs">{config.label}</span>
    </div>
  )
}
