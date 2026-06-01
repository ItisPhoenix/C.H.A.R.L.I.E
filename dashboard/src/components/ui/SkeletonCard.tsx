'use client'

import { cn } from '@/lib/utils'

interface SkeletonCardProps {
  lines?: number
  className?: string
}

export function SkeletonCard({ lines = 3, className }: SkeletonCardProps) {
  return (
    <div className={cn('glass-card p-6', className)}>
      <div className="skeleton h-5 w-1/3 mb-4 rounded" />
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="skeleton h-3 rounded mb-2"
          style={{ width: `${60 + Math.random() * 30}%` }}
        />
      ))}
    </div>
  )
}
