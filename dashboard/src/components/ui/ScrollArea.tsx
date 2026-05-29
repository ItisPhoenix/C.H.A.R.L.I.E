'use client'

import { cn } from '@/lib/utils'

interface ScrollAreaProps {
  children: React.ReactNode
  maxHeight?: string
  className?: string
}

export function ScrollArea({ children, maxHeight = '600px', className }: ScrollAreaProps) {
  return (
    <div
      className={cn('overflow-y-auto', className)}
      style={{ maxHeight }}
    >
      {children}
    </div>
  )
}
