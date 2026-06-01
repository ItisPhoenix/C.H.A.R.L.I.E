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
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-sans text-xl font-medium text-charlie-text tracking-tight">
            {title}
          </h1>
          {subtitle && <p className="text-charlie-dim text-sm mt-1 font-body">{subtitle}</p>}
        </div>
        {actions && <div className="flex items-center gap-2 flex-shrink-0 pt-0.5">{actions}</div>}
      </div>
      {/* Neon gradient line */}
      <div className="absolute bottom-0 left-0 right-0 h-px bg-white/10" />
    </div>
  )
}
