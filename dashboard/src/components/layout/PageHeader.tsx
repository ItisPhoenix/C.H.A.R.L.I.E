'use client'

import { cn } from '@/lib/utils'

interface PageHeaderProps {
  title: string
  subtitle?: string
  actions?: React.ReactNode
  className?: string
}

export function PageHeader({ title, subtitle, actions, className }: PageHeaderProps) {
  return (
    <div className={cn('relative mb-6 pb-4', className)}>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-sans text-xl font-medium text-zinc-100 tracking-tight">
            {title}
          </h1>
          {subtitle && <p className="text-charlie-dim text-sm mt-1 font-body">{subtitle}</p>}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {/* Neon gradient line */}
      <div className="absolute bottom-0 left-0 right-0 h-px bg-white/10" />
    </div>
  )
}
