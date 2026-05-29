'use client'

import { useState } from 'react'
import { Bell } from 'lucide-react'
import { useDashboardStore } from '@/lib/store'

export function NotificationCenter() {
  const [open, setOpen] = useState(false)
  const pendingApprovals = useDashboardStore((s) => s.pendingApprovals)

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="relative p-1 text-charlie-dim hover:text-charlie-text transition-colors cursor-pointer"
      >
        <Bell size={18} />
        {pendingApprovals > 0 && (
          <span className="absolute -top-1 -right-1 bg-charlie-red text-white text-[9px] w-4 h-4 rounded-full flex items-center justify-center font-semibold">
            {pendingApprovals}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-72 glass-card p-3 shadow-neon-cyan z-50">
          <div className="font-display text-xs tracking-[0.1em] text-charlie-cyan uppercase mb-2">
            Notifications
          </div>
          <div className="text-charlie-dim text-sm font-body">
            No new notifications
          </div>
        </div>
      )}
    </div>
  )
}
