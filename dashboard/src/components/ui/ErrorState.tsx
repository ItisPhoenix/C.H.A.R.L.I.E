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
    <div className={cn('flex flex-col items-center justify-center py-12 text-center glass-card border-charlie-red/20 rounded-2xl p-8', className)} style={{ boxShadow: '0 0 20px color-mix(in srgb, var(--charlie-red) 5%, transparent)' }}>
      <div className="text-charlie-red text-lg mb-2 font-display font-bold tracking-wide">Error</div>
      <p className="text-charlie-dim text-sm max-w-md mb-4 font-body">{error}</p>
      {onRetry && (
        <Button variant="ghost" size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  )
}
