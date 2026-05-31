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
          <span className="absolute -top-1 -right-1 bg-gradient-to-br from-charlie-red to-charlie-orange text-white text-[9px] w-4 h-4 rounded-full flex items-center justify-center font-semibold shadow-[0_2px_8px_rgba(239,68,68,0.3)]">
            {pendingApprovals}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-72 glass-card p-4 shadow-neon-glow z-50 rounded-2xl border-charlie-cyan/20 translate-y-[-8px] animate-fade-in-up">
          <div className="font-display text-xs tracking-[0.1em] text-charlie-cyan uppercase mb-2">
            Notifications
          </div>
          <div className="text-charlie-dim text-sm font-body">
            No new signals
          </div>
        </div>
      )}
    </div>
  )
}
