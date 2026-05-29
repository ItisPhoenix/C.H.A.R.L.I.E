'use client'

import { cn } from '@/lib/utils'

interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg'
  label?: string
  className?: string
}

const sizeMap = {
  sm: 16,
  md: 24,
  lg: 36,
}

export function LoadingSpinner({ size = 'md', label, className }: LoadingSpinnerProps) {
  const px = sizeMap[size]

  return (
    <div className={cn('flex items-center gap-3', className)}>
      <div
        className="animate-hex-spin"
        style={{ width: px, height: px }}
      >
        <svg viewBox="0 0 100 100" width={px} height={px}>
          <polygon
            points="50,2 93,25 93,75 50,98 7,75 7,25"
            fill="none"
            stroke="rgba(0, 212, 255, 0.6)"
            strokeWidth="4"
            strokeLinejoin="round"
          />
          <polygon
            points="50,2 93,25 93,75 50,98 7,75 7,25"
            fill="none"
            stroke="rgba(0, 212, 255, 1)"
            strokeWidth="4"
            strokeLinejoin="round"
            strokeDasharray="80 220"
            className="origin-center"
            style={{ filter: 'drop-shadow(0 0 4px rgba(0, 212, 255, 0.5))' }}
          />
        </svg>
      </div>
      {label && <span className="text-charlie-dim text-sm font-body">{label}</span>}
    </div>
  )
}
