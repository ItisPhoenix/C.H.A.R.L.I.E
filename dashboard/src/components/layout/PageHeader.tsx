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
          <h1 className="font-display text-2xl font-bold text-charlie-cyan tracking-wide">
            {title}
          </h1>
          {subtitle && <p className="text-charlie-dim text-sm mt-1 font-body">{subtitle}</p>}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {/* Neon gradient line */}
      <div
        className="absolute bottom-0 left-0 right-0 h-px"
        style={{
          background: 'linear-gradient(90deg, rgba(0, 212, 255, 0.4), rgba(0, 212, 255, 0.1), transparent)',
        }}
      />
    </div>
  )
}
