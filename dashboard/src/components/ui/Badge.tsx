'use client'

import { cn } from '@/lib/utils'

type BadgeVariant = 'cyan' | 'green' | 'amber' | 'red' | 'dim' | 'orange' | 'purple'

const variantStyles: Record<BadgeVariant, string> = {
  cyan: 'bg-charlie-cyan/10 text-charlie-cyan/80',
  green: 'bg-charlie-green/10 text-charlie-green/80',
  amber: 'bg-charlie-amber/10 text-charlie-amber/80',
  orange: 'bg-charlie-orange/10 text-charlie-orange/80',
  red: 'bg-charlie-red/10 text-charlie-red/80',
  dim: 'bg-charlie-card/50 text-charlie-dim/70',
  purple: 'bg-charlie-purple/10 text-charlie-purple/80',
}

interface BadgeProps {
  variant?: BadgeVariant
  children: React.ReactNode
  className?: string
}

export function Badge({ variant = 'cyan', children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 text-[11px] font-medium rounded-md font-sans',
        variantStyles[variant],
        className,
      )}
    >
      {children}
    </span>
  )
}
