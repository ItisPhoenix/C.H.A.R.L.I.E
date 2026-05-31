'use client'

import { cn } from '@/lib/utils'
import { Button } from './Button'

interface EmptyStateProps {
  icon?: React.ReactNode
  title: string
  description?: string
  action?: { label: string; onClick: () => void }
  terminal?: boolean
  className?: string
}

export function EmptyState({ icon, title, description, action, terminal, className }: EmptyStateProps) {
  // Terminal style when no action or explicitly requested
  const useTerminal = terminal ?? !action

  if (useTerminal) {
    return (
      <div className={cn('flex flex-col items-center justify-center py-12', className)}>
        <div className="font-mono text-charlie-dim/60 text-sm text-center space-y-1 bg-charlie-card/30 p-4 rounded-xl border border-charlie-border/20 shadow-inner">
          <span className="text-charlie-cyan/40">$</span> {title.toLowerCase().replace(/\s+/g, '_')}
          <span className="inline-block w-2 h-4 bg-charlie-cyan/40 ml-1 animate-pulse" />
        </div>
        {description && (
          <p className="font-body text-charlie-dim text-sm mt-3 max-w-md text-center">{description}</p>
        )}
      </div>
    )
  }

  // CTA style
  return (
    <div className={cn('flex flex-col items-center justify-center py-12 text-center', className)}>
      {icon && <div className="text-charlie-dim mb-3">{icon}</div>}
      <h3 className="text-charlie-text font-medium mb-1 font-display tracking-wide">{title}</h3>
      {description && <p className="text-charlie-dim text-sm max-w-md font-body">{description}</p>}
      {action && (
        <Button onClick={action.onClick} className="mt-4">
          {action.label}
        </Button>
      )}
    </div>
  )
}
