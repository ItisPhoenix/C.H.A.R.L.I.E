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
    <div className={cn('flex flex-col items-center justify-center py-12 text-center', className)}>
      <div className="text-charlie-red text-lg mb-2">Connection Failed</div>
      <p className="text-charlie-dim text-sm max-w-md mb-4">{error}</p>
      {onRetry && (
        <Button variant="ghost" size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  )
}
