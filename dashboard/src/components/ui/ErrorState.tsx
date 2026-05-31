'use client'

import { Button } from './Button'
import { cn } from '@/lib/utils'

interface ErrorStateProps {
  error: string
  onRetry?: () => void
  className?: string
}

export function ErrorState({ error, onRetry, className }: ErrorStateProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center py-12 text-center glass-card border-charlie-red/20 shadow-[0_0_20px_rgba(239,68,68,0.05)] rounded-2xl p-8', className)}>
      <div className="text-charlie-red text-lg mb-2 font-display font-bold tracking-wide">Connection Inhibited</div>
      <p className="text-charlie-dim text-sm max-w-md mb-4 font-body">{error}</p>
      {onRetry && (
        <Button variant="ghost" size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  )
}
